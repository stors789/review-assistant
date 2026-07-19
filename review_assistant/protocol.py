"""Configuration models and validation for protocol-driven reviews."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

from .io_utils import load_yaml, stable_hash


class ConfigurationError(ValueError):
    """Raised when a project configuration is invalid."""


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must be a mapping")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{path} must be a list")
    return value


@dataclass(frozen=True)
class Protocol:
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Protocol":
        return cls.validate(load_yaml(path))

    @classmethod
    def validate(cls, data: dict[str, Any]) -> "Protocol":
        review = _mapping(data.get("review"), "review")
        scope = _mapping(data.get("scope"), "scope")
        eligibility = _mapping(data.get("eligibility"), "eligibility")
        synthesis = _mapping(data.get("synthesis"), "synthesis")
        for field in ("title", "type", "primary_question"):
            if not isinstance(review.get(field), str):
                raise ConfigurationError(f"review.{field} must be a string")
        _list(review.get("secondary_questions", []), "review.secondary_questions")
        for field in (
            "populations", "core_interventions", "adjacent_interventions", "comparators",
            "primary_outcomes", "secondary_outcomes", "include_study_types", "exclude_study_types",
        ):
            _list(scope.get(field, []), f"scope.{field}")
        _list(eligibility.get("inclusion_criteria", []), "eligibility.inclusion_criteria")
        _list(eligibility.get("exclusion_criteria", []), "eligibility.exclusion_criteria")
        _list(synthesis.get("required_sections", []), "synthesis.required_sections")
        _list(synthesis.get("required_questions", []), "synthesis.required_questions")
        enforcement = data.get("screening", {}).get("enforcement", "optional")
        if enforcement not in {"required", "optional", "disabled"}:
            raise ConfigurationError("screening.enforcement must be required, optional, or disabled")
        fulltext = data.get("fulltext", {})
        if not isinstance(fulltext, dict):
            raise ConfigurationError("fulltext must be a mapping")
        fulltext_requirement = fulltext.get("requirement", "required")
        if fulltext_requirement not in {"required", "structured_extraction_allowed", "disabled"}:
            raise ConfigurationError("fulltext.requirement must be required, structured_extraction_allowed, or disabled")
        return cls(data=data)

    @property
    def hash(self) -> str:
        return stable_hash(self.data)


ALLOWED_FIELD_TYPES = {"string", "number", "integer", "boolean", "enum", "list", "object"}


@dataclass(frozen=True)
class ExtractionSchema:
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "ExtractionSchema":
        return cls.validate(load_yaml(path))

    @classmethod
    def validate(cls, data: dict[str, Any]) -> "ExtractionSchema":
        fields = _mapping(data.get("fields"), "fields")
        if not fields:
            raise ConfigurationError("fields must contain at least one extraction field")
        for name, spec in fields.items():
            cls._validate_field(str(name), _mapping(spec, f"fields.{name}"))
        outcome_schema = data.get("outcome_schema")
        if outcome_schema is not None:
            for name, spec in _mapping(outcome_schema, "outcome_schema").items():
                cls._validate_field(f"outcome_schema.{name}", _mapping(spec, f"outcome_schema.{name}"))
        outcome_identity = data.get("outcome_identity")
        if outcome_identity is not None:
            identity = _mapping(outcome_identity, "outcome_identity")
            fields = identity.get("fields", [])
            _list(fields, "outcome_identity.fields")
            if any(not isinstance(field, str) or not field.strip() for field in fields):
                raise ConfigurationError("outcome_identity.fields must contain non-empty strings")
            fallback = identity.get("fallback", "domain_and_ordinal")
            if not isinstance(fallback, str) or fallback not in {"domain_and_ordinal", "domain", "domain_only"}:
                raise ConfigurationError("outcome_identity.fallback must be domain_and_ordinal or domain")
        return cls(data=data)

    @classmethod
    def _validate_field(cls, name: str, spec: dict[str, Any]) -> None:
        kind = spec.get("type")
        if kind not in ALLOWED_FIELD_TYPES:
            raise ConfigurationError(f"field {name} has unsupported type {kind!r}")
        if "required" in spec and not isinstance(spec["required"], bool):
            raise ConfigurationError(f"field {name}.required must be boolean")
        if kind == "enum" and not isinstance(spec.get("values"), list):
            raise ConfigurationError(f"enum field {name} requires values")
        if kind == "list" and "item_schema" not in spec:
            raise ConfigurationError(f"list field {name} requires item_schema")
        if kind == "list" and isinstance(spec.get("item_schema"), dict):
            for nested_name, nested_spec in spec["item_schema"].items():
                cls._validate_field(f"{name}.{nested_name}", _mapping(nested_spec, nested_name))
        if kind == "object":
            nested = _mapping(spec.get("fields"), f"field {name}.fields")
            for nested_name, nested_spec in nested.items():
                cls._validate_field(f"{name}.{nested_name}", _mapping(nested_spec, nested_name))

    @property
    def hash(self) -> str:
        return stable_hash(self.data)

    def apply_missing_values(self, values: dict[str, Any]) -> dict[str, Any]:
        result = dict(values)
        for path, spec in self.data["fields"].items():
            if path not in result:
                if "default" in spec:
                    result[path] = spec["default"]
                elif spec.get("required"):
                    result[path] = spec.get("missing_value", "not_reported")
        return result

    @property
    def outcome_schema(self) -> dict[str, Any]:
        configured = self.data.get("outcome_schema")
        if isinstance(configured, dict):
            return configured
        legacy = self.data.get("fields", {}).get("outcomes", {}).get("item_schema", {})
        normalized = dict(legacy) if isinstance(legacy, dict) else {}
        if "direction" in normalized and "effect_direction" not in normalized:
            normalized["effect_direction"] = normalized.pop("direction")
        if "evidence_quote" in normalized:
            normalized.pop("evidence_quote")
            normalized.setdefault("evidence", {"type": "list", "required": True, "item_schema": {
                "quote": {"type": "string", "required": True},
            }})
        normalized.setdefault("domain", {"type": "string", "required": True})
        normalized.setdefault("effect_direction", {"type": "enum", "values": ["increase", "decrease", "no_change", "mixed", "unclear"], "required": True})
        normalized.setdefault("support_relation", {"type": "enum", "values": ["supports", "contradicts", "neutral", "mixed", "unclear"], "required": True})
        normalized.setdefault("evidence", {"type": "list", "required": True, "item_schema": {
            "quote": {"type": "string", "required": True},
        }})
        return normalized

    @property
    def outcome_identity(self) -> dict[str, Any]:
        configured = self.data.get("outcome_identity")
        if not isinstance(configured, dict):
            return {"fields": [], "fallback": "domain_and_ordinal"}
        return {
            "fields": list(configured.get("fields", [])),
            "fallback": str(configured.get("fallback", "domain_and_ordinal")),
        }

    def apply_study_missing_values(self, values: dict[str, Any]) -> dict[str, Any]:
        result = dict(values)
        for path, spec in self.data["fields"].items():
            if path == "outcomes":
                continue
            if path not in result:
                if "default" in spec:
                    result[path] = spec["default"]
                elif spec.get("required"):
                    result[path] = spec.get("missing_value", "not_reported")
        return result

    def validate_study_values(self, values: dict[str, Any]) -> list[dict[str, str]]:
        return self._validate_mapping(values, {k: v for k, v in self.data["fields"].items() if k != "outcomes"})

    def validate_outcome(self, value: dict[str, Any]) -> list[dict[str, str]]:
        return self._validate_mapping(value, self.outcome_schema)

    def _validate_mapping(self, values: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        for path, spec in schema.items():
            missing = spec.get("missing_value", "not_reported")
            if spec.get("required") and path not in values:
                errors.append({"field": path, "error": "required"})
                continue
            value = values.get(path)
            if value is None or value == missing:
                continue
            self._validate_value(path, value, spec, errors)
        return errors

    def validate_values(self, values: dict[str, Any]) -> list[dict[str, str]]:
        return self._validate_mapping(values, self.data["fields"])

    @classmethod
    def _validate_value(cls, path: str, value: Any, spec: dict[str, Any], errors: list[dict[str, str]]) -> None:
        kind = spec["type"]
        matches = {
            "string": isinstance(value, str), "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool), "boolean": isinstance(value, bool),
            "enum": value in spec.get("values", []), "list": isinstance(value, list), "object": isinstance(value, dict),
        }[kind]
        if not matches:
            errors.append({"field": path, "error": f"expected_{kind}"})
            return
        if kind == "list" and isinstance(spec.get("item_schema"), dict):
            for index, item in enumerate(value):
                if not isinstance(item, dict):
                    errors.append({"field": f"{path}[{index}]", "error": "expected_object"})
                    continue
                for nested_name, nested_spec in spec["item_schema"].items():
                    nested_value = item.get(nested_name, nested_spec.get("missing_value", "not_reported"))
                    if nested_spec.get("required") and nested_name not in item:
                        errors.append({"field": f"{path}[{index}].{nested_name}", "error": "required"})
                    elif nested_value != nested_spec.get("missing_value", "not_reported"):
                        cls._validate_value(f"{path}[{index}].{nested_name}", nested_value, nested_spec, errors)
        rule = spec.get("validation_rule", {})
        if isinstance(rule, dict):
            if "min" in rule and isinstance(value, (int, float)) and value < rule["min"]:
                errors.append({"field": path, "error": "below_minimum"})
            if "max" in rule and isinstance(value, (int, float)) and value > rule["max"]:
                errors.append({"field": path, "error": "above_maximum"})
            if "pattern" in rule and isinstance(value, str) and re.search(str(rule["pattern"]), value) is None:
                errors.append({"field": path, "error": "pattern_mismatch"})
