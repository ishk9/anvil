"""Shared helpers for encoding images into LLM message payloads."""

from __future__ import annotations

import base64

from domain.models.conversation import ContentImage


def encode_image_base64(image: ContentImage) -> str:
    return base64.standard_b64encode(image.path.read_bytes()).decode("ascii")


def data_uri(image: ContentImage) -> str:
    return f"data:{image.media_type};base64,{encode_image_base64(image)}"
