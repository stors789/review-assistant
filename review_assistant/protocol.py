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

    def validate_values(self, values: dict[str, Any]) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        for path, spec in self.data["fields"].items():
            value = values.get(path)
            missing = spec.get("missing_value", "not_reported")
            if value is None or value == missing:
                continue
            self._validate_value(path, value, spec, errors)
        return errors

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
