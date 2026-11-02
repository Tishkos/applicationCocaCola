"""A minimal stand-in for the two things the registry takes from pydantic (BaseModel, Field), so the measuring
session can run on a PC where only pymodbus and PyYAML can be installed (slow site internet). Coerces the basic
field types, turns nested dicts into the declared model, ignores unknown keys - as the registry needs, no more."""

from __future__ import annotations

import copy
import typing

_MODELS: dict[str, type] = {}
_MISSING = object()


class _FieldInfo:
    def __init__(self, default=_MISSING, default_factory=None):
        self.default, self.default_factory = default, default_factory


def Field(default=_MISSING, default_factory=None, **_):  # noqa: N802 - same name as pydantic
    return _FieldInfo(default, default_factory)


def _coerce(ann: str, value):
    ann = ann.replace(" ", "")
    if value is None:
        return None
    if ann.startswith("list[") and ann[5:-1] in _MODELS:
        model = _MODELS[ann[5:-1]]
        return [v if isinstance(v, model) else model(**v) for v in value]
    if ann == "float":
        return float(value)
    if ann == "int":
        return int(value)
    if ann == "str":
        return str(value)
    return value


class BaseModel:
    model_fields: dict[str, object] = {}

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        _MODELS[cls.__name__] = cls
        anns = {}
        for base in reversed(cls.__mro__):
            anns.update(getattr(base, "__annotations__", {}))
        cls._anns = {k: (v if isinstance(v, str) else getattr(v, "__name__", str(v))) for k, v in anns.items()
                     if not k.startswith("_") and k != "model_fields"}
        cls.model_fields = {k: None for k in cls._anns}

    def __init__(self, **data):
        for name, ann in self._anns.items():
            if name in data:
                setattr(self, name, _coerce(ann, data[name]))
                continue
            d = getattr(type(self), name, _MISSING)
            if isinstance(d, _FieldInfo):
                d = d.default_factory() if d.default_factory else d.default
            if d is _MISSING:
                raise TypeError(f"{type(self).__name__}: missing field '{name}'")
            setattr(self, name, copy.copy(d))

    def __repr__(self):
        return f"{type(self).__name__}(" + ", ".join(f"{k}={getattr(self, k)!r}" for k in self._anns) + ")"


__all__ = ["BaseModel", "Field", "typing"]
