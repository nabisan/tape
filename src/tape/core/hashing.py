from __future__ import annotations

import hashlib
import json
from typing import NewType

from pydantic import BaseModel

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None

Digest = NewType("Digest", str)


def canonical(obj: BaseModel | Json) -> bytes:
    value = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode()


def digest(obj: BaseModel | Json) -> Digest:
    return digest_bytes(canonical(obj))


def digest_bytes(data: bytes | memoryview) -> Digest:
    return Digest(hashlib.blake2b(data, digest_size=16).hexdigest())
