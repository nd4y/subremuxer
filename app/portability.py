"""Export and import of profiles and templates, in JSON or YAML.

The document is versioned and self-describing so a file can be recognised on
import without the user having to say what it is.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from .aggregates import (
    AggregateError,
    AggregateRepository,
    parse_sources,
    validate_aggregate_payload,
)
from .profiles import ProfileError, ProfileRepository, unique_name, validate_profile_payload
from .templates import TemplateError, TemplateRepository, validate_payload

EXPORT_VERSION = 1
KIND_PROFILE = "subremuxer.profile"
KIND_BUNDLE = "subremuxer.bundle"

#: Everything that defines a profile, minus the database id and timestamps.
PROFILE_FIELDS = (
    "name",
    "upstream_url",
    "enabled",
    "hwid_mode",
    "hwid",
    "device_os",
    "device_ver",
    "device_model",
    "upstream_ua",
    "filter",
    "protocols",
    "output_format",
    "cache_ttl",
)


#: Everything that defines an aggregate. Its sources travel as profile *names*,
#: not ids: a bundle has to survive being restored onto an empty instance, where
#: the same profiles will be numbered differently.
AGGREGATE_FIELDS = (
    "name",
    "enabled",
    "prefix_names",
    "dedupe",
    "output_format",
)


class PortabilityError(ValueError):
    pass


def _profile_document(profile: dict[str, Any], *, with_token: bool) -> dict[str, Any]:
    data = {key: profile.get(key) for key in PROFILE_FIELDS}
    if with_token:
        data["token"] = profile.get("token")
    return data


def _template_document(template: dict[str, Any]) -> dict[str, Any]:
    document: dict[str, Any] = {"name": template["name"]}
    # Kept so the config editor can rename a shipped template in place instead of
    # deleting it and creating a look-alike.
    if template.get("builtin_id"):
        document["builtin_id"] = template["builtin_id"]
    document["description"] = template.get("description", "")
    document["payload"] = template.get("payload", {})
    return document


def export_profile(profile: dict[str, Any], *, with_token: bool = True) -> dict[str, Any]:
    return {
        "kind": KIND_PROFILE,
        "version": EXPORT_VERSION,
        "profile": _profile_document(profile, with_token=with_token),
    }


#: Instance-wide settings that travel with a full backup. The admin password is
#: environment-only and deliberately not among them.
SETTINGS_FIELDS = (
    "default_hwid",
    "default_device_os",
    "default_device_ver",
    "default_device_model",
    "probe_token",
)


def _aggregate_document(
    aggregate: dict[str, Any], profile_names: dict[int, str], *, with_token: bool
) -> dict[str, Any]:
    data = {key: aggregate.get(key) for key in AGGREGATE_FIELDS}
    data["sources"] = [
        {"profile": profile_names.get(int(source["profile_id"]), ""), "prefix": source["prefix"]}
        for source in aggregate.get("sources") or []
        if int(source["profile_id"]) in profile_names
    ]
    if with_token:
        data["token"] = aggregate.get("token")
    return data


def export_bundle(
    profiles: list[dict[str, Any]],
    templates: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
    aggregates: list[dict[str, Any]] | None = None,
    *,
    with_tokens: bool = True,
) -> dict[str, Any]:
    """The whole configuration: settings, templates, profiles and aggregates."""
    profile_names = {int(item["id"]): str(item["name"]) for item in profiles if "id" in item}
    return {
        "kind": KIND_BUNDLE,
        "version": EXPORT_VERSION,
        "settings": {
            key: settings.get(key, "") for key in SETTINGS_FIELDS if settings and key in settings
        },
        "templates": [_template_document(item) for item in templates],
        "profiles": [_profile_document(item, with_token=with_tokens) for item in profiles],
        "aggregates": [
            _aggregate_document(item, profile_names, with_token=with_tokens)
            for item in aggregates or []
        ],
    }


def dump(document: dict[str, Any], fmt: str) -> str:
    if fmt == "yaml":
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=4096)
    if fmt == "json":
        return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    raise PortabilityError(f"неизвестный формат экспорта: {fmt}")


def parse(content: str) -> dict[str, Any]:
    """Accept JSON or YAML — JSON is valid YAML, but try it first for clearer errors."""
    text = (content or "").strip()
    if not text:
        raise PortabilityError("пустой файл")
    data: Any
    if text[0] in "{[":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PortabilityError(f"не удалось разобрать JSON: {exc}") from exc
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise PortabilityError(f"не удалось разобрать YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise PortabilityError("ожидался объект с полем kind")
    kind = str(data.get("kind", ""))
    if kind not in {KIND_PROFILE, KIND_BUNDLE}:
        raise PortabilityError(
            "файл не похож на конфигурацию Sub Remuxer: ожидалось поле "
            f"kind = {KIND_PROFILE} или {KIND_BUNDLE}"
        )
    version = int(data.get("version") or 0)
    if version > EXPORT_VERSION:
        raise PortabilityError(
            f"файл сделан более новой версией приложения (version {version})"
        )
    return data


def _aggregate_payload(entry: dict[str, Any], profile_ids: dict[str, int]) -> dict[str, Any]:
    """Turn a document entry into a payload the repository understands."""
    payload = {key: entry.get(key) for key in AGGREGATE_FIELDS if key in entry}
    raw = entry.get("sources")
    if raw is not None and not isinstance(raw, list):
        raise PortabilityError("список источников должен быть массивом")
    sources: list[Any] = []
    for item in raw or []:
        if isinstance(item, dict) and "profile" in item:
            name = str(item.get("profile") or "")
            if name not in profile_ids:
                raise PortabilityError(f"источник «{name}» не найден среди профилей")
            sources.append(
                {"profile_id": profile_ids[name], "prefix": str(item.get("prefix") or "")}
            )
        else:
            # A hand-written file may address a profile by its id directly.
            sources.append(item)
    payload["sources"] = sources
    return payload


def _placeholder_sources(sources: list[Any]) -> list[Any]:
    """The same list with profile names swapped for stand-in ids, one per name."""
    numbers: dict[str, int] = {}
    result: list[Any] = []
    for item in sources:
        if isinstance(item, dict) and "profile" in item:
            key = str(item.get("profile") or "")
            numbers.setdefault(key, len(numbers) + 1)
            result.append({"profile_id": numbers[key], "prefix": item.get("prefix")})
        else:
            result.append(item)
    return result


def _unique_name(name: str, taken: set[str]) -> str:
    return unique_name(
        name, taken, marker="импорт", fallback="Импортированный профиль", keep_base=True
    )


class Importer:
    """Applies a parsed document, reporting what happened per entry."""

    def __init__(
        self,
        profiles: ProfileRepository,
        templates: TemplateRepository,
        db: Any = None,
        aggregates: AggregateRepository | None = None,
    ) -> None:
        self.profiles = profiles
        self.templates = templates
        self.db = db
        self.aggregates = aggregates

    def apply(
        self, document: dict[str, Any], *, keep_tokens: bool = False, with_settings: bool = True
    ) -> dict[str, Any]:
        kind = document["kind"]
        raw_profiles = (
            [document.get("profile")] if kind == KIND_PROFILE else document.get("profiles") or []
        )
        raw_templates = document.get("templates") or [] if kind == KIND_BUNDLE else []
        raw_aggregates = document.get("aggregates") or [] if kind == KIND_BUNDLE else []

        if (
            not isinstance(raw_profiles, list)
            or not isinstance(raw_templates, list)
            or not isinstance(raw_aggregates, list)
        ):
            raise PortabilityError("повреждённая структура файла")

        created_profiles: list[dict[str, Any]] = []
        created_templates: list[dict[str, Any]] = []
        created_aggregates: list[dict[str, Any]] = []
        errors: list[str] = []

        taken = {profile.name for profile in self.profiles.list()}
        existing_tokens = {profile.token for profile in self.profiles.list()}
        # Sources are written down by name; renaming on import must not break
        # them, so the *document's* name is what maps to the created profile.
        profile_ids = {profile.name: profile.id for profile in self.profiles.list()}

        for entry in raw_profiles:
            if not isinstance(entry, dict):
                errors.append("профиль пропущен: ожидался объект")
                continue
            payload = {key: entry.get(key) for key in PROFILE_FIELDS if key in entry}
            original_name = str(payload.get("name") or "")
            payload["name"] = _unique_name(original_name, taken)
            token = str(entry.get("token") or "")
            if keep_tokens and token and token not in existing_tokens:
                payload["token"] = token
            try:
                profile = self.profiles.create(payload)
            except (ProfileError, ValueError) as exc:
                errors.append(f"профиль «{payload['name']}»: {exc}")
                continue
            taken.add(profile.name)
            existing_tokens.add(profile.token)
            profile_ids[original_name] = profile.id
            created_profiles.append(profile.as_dict())

        template_names = {template.name for template in self.templates.list()}
        for entry in raw_templates:
            if not isinstance(entry, dict):
                errors.append("шаблон пропущен: ожидался объект")
                continue
            name = str(entry.get("name") or "Импортированный шаблон")
            if name in template_names:
                name = _unique_name(name, template_names)
            try:
                payload = validate_payload(entry.get("payload"))
                template = self.templates.create(
                    {
                        "name": name,
                        "description": str(entry.get("description") or ""),
                        "payload": payload,
                    }
                )
            except (TemplateError, ValueError) as exc:
                errors.append(f"шаблон «{name}»: {exc}")
                continue
            template_names.add(template.name)
            created_templates.append(template.as_dict())

        if self.aggregates is not None:
            aggregate_names = {item.name for item in self.aggregates.list()}
            aggregate_tokens = {item.token for item in self.aggregates.list()}
            for entry in raw_aggregates:
                if not isinstance(entry, dict):
                    errors.append("сборка пропущена: ожидался объект")
                    continue
                name = str(entry.get("name") or "Импортированная сборка")
                try:
                    payload = _aggregate_payload(entry, profile_ids)
                except PortabilityError as exc:
                    errors.append(f"сборка «{name}»: {exc}")
                    continue
                payload["name"] = _unique_name(name, aggregate_names)
                token = str(entry.get("token") or "")
                if keep_tokens and token and token not in aggregate_tokens:
                    payload["token"] = token
                try:
                    aggregate = self.aggregates.create(payload)
                except (AggregateError, ValueError) as exc:
                    errors.append(f"сборка «{payload['name']}»: {exc}")
                    continue
                aggregate_names.add(aggregate.name)
                aggregate_tokens.add(aggregate.token)
                created_aggregates.append(aggregate.as_dict())

        settings_applied: list[str] = []
        raw_settings = document.get("settings")
        if with_settings and self.db is not None and isinstance(raw_settings, dict):
            for key in SETTINGS_FIELDS:
                if key not in raw_settings:
                    continue
                value = str(raw_settings[key] or "").strip()
                if key == "probe_token" and not value:
                    continue
                self.db.set_setting(key, value)
                settings_applied.append(key)

        return {
            "profiles_created": len(created_profiles),
            "templates_created": len(created_templates),
            "aggregates_created": len(created_aggregates),
            "settings_applied": settings_applied,
            "profiles": created_profiles,
            "templates": created_templates,
            "aggregates": created_aggregates,
            "errors": errors,
        }


# ---------------------------------------------------------------- full apply


class ConfigApplier:
    """Makes the instance match an edited bundle, instead of merging into it.

    This is what the built-in editor saves through: what you see in the document
    is what the instance ends up being. Everything is validated first, so a typo
    on the last profile cannot leave the first half applied.
    """

    def __init__(
        self,
        profiles: ProfileRepository,
        templates: TemplateRepository,
        db: Any,
        aggregates: AggregateRepository | None = None,
    ) -> None:
        self.profiles = profiles
        self.templates = templates
        self.db = db
        self.aggregates = aggregates

    # -- planning ---------------------------------------------------------

    def plan(self, document: dict[str, Any]) -> dict[str, Any]:
        """Validate the whole document and describe what applying it would do."""
        if document.get("kind") != KIND_BUNDLE:
            raise PortabilityError(
                "редактор работает с полной конфигурацией "
                f"(kind: {KIND_BUNDLE}); для одного профиля используйте импорт"
            )

        raw_profiles = document.get("profiles") or []
        raw_templates = document.get("templates") or []
        if not isinstance(raw_profiles, list) or not isinstance(raw_templates, list):
            raise PortabilityError("поля profiles и templates должны быть списками")

        errors: list[str] = []
        existing_profiles = self.profiles.list()
        by_token = {profile.token: profile for profile in existing_profiles}
        by_name = {profile.name: profile for profile in existing_profiles}

        matched_ids: set[int] = set()
        planned_profiles: list[tuple[Any, dict[str, Any]]] = []
        seen_names: set[str] = set()

        for index, entry in enumerate(raw_profiles, start=1):
            if not isinstance(entry, dict):
                errors.append(f"профиль #{index}: ожидался объект")
                continue
            payload = {key: entry.get(key) for key in PROFILE_FIELDS if key in entry}
            name = str(payload.get("name") or "").strip()
            if name in seen_names:
                errors.append(f"профиль «{name}»: имя встречается дважды")
                continue
            seen_names.add(name)

            token = str(entry.get("token") or "")
            match = by_token.get(token) or (by_name.get(name) if not token else None)
            if match is not None and match.id in matched_ids:
                match = None
            if match is not None:
                matched_ids.add(match.id)
                payload["token"] = match.token
            elif token:
                payload["token"] = token

            try:
                # Reuse the profile layer's own validation, without writing.
                validate_profile_payload(payload)
            except ValueError as exc:
                errors.append(f"профиль «{name or index}»: {exc}")
                continue
            planned_profiles.append((match, payload))

        planned_templates: list[tuple[Any, dict[str, Any]]] = []
        existing_templates = self.templates.list()
        templates_by_builtin = {t.builtin_id: t for t in existing_templates if t.builtin_id}
        templates_by_name = {t.name: t for t in existing_templates}
        matched_template_ids: set[int] = set()

        for index, entry in enumerate(raw_templates, start=1):
            if not isinstance(entry, dict):
                errors.append(f"шаблон #{index}: ожидался объект")
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                errors.append(f"шаблон #{index}: не указано название")
                continue
            builtin_id = entry.get("builtin_id")
            match = (
                templates_by_builtin.get(builtin_id)
                if builtin_id
                else templates_by_name.get(name)
            )
            if match is not None and match.id in matched_template_ids:
                match = None
            if match is not None:
                matched_template_ids.add(match.id)
            try:
                payload = validate_payload(entry.get("payload"))
            except ValueError as exc:
                errors.append(f"шаблон «{name}»: {exc}")
                continue
            planned_templates.append(
                (
                    match,
                    {
                        "name": name,
                        "description": str(entry.get("description") or ""),
                        "payload": payload,
                        "builtin_id": builtin_id,
                    },
                )
            )

        planned_aggregates, removed_aggregates = self._plan_aggregates(
            document, seen_names, errors
        )

        removed_profiles = [
            profile for profile in existing_profiles if profile.id not in matched_ids
        ]
        removed_templates = [
            template for template in existing_templates if template.id not in matched_template_ids
        ]

        raw_settings = document.get("settings")
        settings = {}
        if isinstance(raw_settings, dict):
            settings = {
                key: str(raw_settings[key] or "").strip()
                for key in SETTINGS_FIELDS
                if key in raw_settings
            }

        return {
            "ok": not errors,
            "errors": errors,
            "summary": {
                "profiles_created": sum(1 for match, _ in planned_profiles if match is None),
                "profiles_updated": sum(1 for match, _ in planned_profiles if match is not None),
                "profiles_removed": [profile.name for profile in removed_profiles],
                "templates_created": sum(1 for match, _ in planned_templates if match is None),
                "templates_updated": sum(1 for match, _ in planned_templates if match is not None),
                "templates_removed": [template.name for template in removed_templates],
                "aggregates_created": sum(1 for match, _ in planned_aggregates if match is None),
                "aggregates_updated": sum(
                    1 for match, _ in planned_aggregates if match is not None
                ),
                "aggregates_removed": [item.name for item in removed_aggregates],
                "settings_changed": sorted(settings),
            },
            "_plan": {
                "profiles": planned_profiles,
                "templates": planned_templates,
                "aggregates": planned_aggregates,
                "removed_profiles": removed_profiles,
                "removed_templates": removed_templates,
                "removed_aggregates": removed_aggregates,
                "settings": settings,
            },
        }

    def _plan_aggregates(
        self, document: dict[str, Any], profile_names: set[str], errors: list[str]
    ) -> tuple[list[tuple[Any, dict[str, Any]]], list[Any]]:
        """Validate the document's aggregates against the profiles it declares.

        Sources are checked against the *document*, not against the instance: a
        full apply removes any profile the document leaves out, so a source that
        only exists on this instance today would break tomorrow.
        """
        if self.aggregates is None:
            return [], []
        raw = document.get("aggregates") or []
        if not isinstance(raw, list):
            errors.append("поле aggregates должно быть списком")
            return [], []

        existing = self.aggregates.list()
        by_token = {item.token: item for item in existing}
        by_name = {item.name: item for item in existing}
        matched_ids: set[int] = set()
        planned: list[tuple[Any, dict[str, Any]]] = []
        seen: set[str] = set()

        for index, entry in enumerate(raw, start=1):
            if not isinstance(entry, dict):
                errors.append(f"сборка #{index}: ожидался объект")
                continue
            name = str(entry.get("name") or "").strip()
            if name in seen:
                errors.append(f"сборка «{name}»: имя встречается дважды")
                continue
            seen.add(name)

            token = str(entry.get("token") or "")
            match = by_token.get(token) or (by_name.get(name) if not token else None)
            if match is not None and match.id in matched_ids:
                match = None
            if match is not None:
                matched_ids.add(match.id)

            # Names are resolved to ids only when applying — profiles the
            # document creates do not have ids yet.
            payload = {key: entry.get(key) for key in AGGREGATE_FIELDS if key in entry}
            sources = entry.get("sources") or []
            if not isinstance(sources, list):
                errors.append(f"сборка «{name or index}»: список источников должен быть массивом")
                continue
            payload["sources"] = sources

            unknown = [
                str(item.get("profile") or "")
                for item in sources
                if isinstance(item, dict) and str(item.get("profile") or "") not in profile_names
            ]
            if unknown:
                errors.append(f"сборка «{name or index}»: нет профиля {', '.join(unknown)}")
                continue
            try:
                validate_aggregate_payload({**payload, "sources": []})
                # Stand-in ids, so the real rules about duplicates, prefix length
                # and source count still run before anything is written.
                parse_sources(_placeholder_sources(sources))
            except AggregateError as exc:
                errors.append(f"сборка «{name or index}»: {exc}")
                continue
            if match is not None:
                payload["token"] = match.token
            elif token:
                payload["token"] = token
            planned.append((match, payload))

        removed = [item for item in existing if item.id not in matched_ids]
        return planned, removed

    # -- applying ---------------------------------------------------------

    def apply(self, document: dict[str, Any]) -> dict[str, Any]:
        plan = self.plan(document)
        if not plan["ok"]:
            raise PortabilityError("; ".join(plan["errors"]))

        detail = plan.pop("_plan")

        for match, payload in detail["profiles"]:
            if match is None:
                self.profiles.create(payload)
            else:
                self.profiles.update(match.id, payload)

        for profile in detail["removed_profiles"]:
            # Soft delete, so a mistaken save is still recoverable for a day.
            self.profiles.delete(profile.id)

        for match, payload in detail["templates"]:
            if match is None:
                self.templates.create(payload)
            else:
                self.templates.update(match.id, payload)

        for template in detail["removed_templates"]:
            self.templates.delete(template.id)

        if self.aggregates is not None:
            # Now that every profile the document declares exists, its sources
            # can finally be turned from names into ids.
            profile_ids = {profile.name: profile.id for profile in self.profiles.list()}
            for match, payload in detail["aggregates"]:
                resolved = _aggregate_payload(payload, profile_ids)
                if payload.get("token"):
                    resolved["token"] = payload["token"]
                if match is None:
                    self.aggregates.create(resolved)
                else:
                    self.aggregates.update(match.id, resolved)

            for aggregate in detail["removed_aggregates"]:
                self.aggregates.delete(aggregate.id)

        for key, value in detail["settings"].items():
            if key == "probe_token" and not value:
                continue
            self.db.set_setting(key, value)

        return plan
