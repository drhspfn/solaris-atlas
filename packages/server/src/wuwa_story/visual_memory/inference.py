"""Offline DINOv2 embeddings and upstream YOLOX character detection.

Set VISUAL_MEMORY_DINO_PATH to a local Hugging Face DINOv2-small directory and
VISUAL_MEMORY_DETECTOR_PATH to the upstream character.onnx file. Install the
server's visual-memory extra. Models are loaded lazily, on CPU, without downloads.
"""

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image


class VisualMemoryUnavailable(RuntimeError):
    """The requested local model or its inference dependencies are unavailable."""


@dataclass(frozen=True)
class EmbeddingResult:
    model_key: str
    vector: list[float]
    config: dict[str, Any]


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class VisualMemoryEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = self._processor = self._session = None
        self._config: dict[str, Any] = {}

    def _load_embedder(self) -> None:
        if self._model is not None:
            return
        path = Path(os.environ.get("VISUAL_MEMORY_DINO_PATH", ""))
        if not os.environ.get("VISUAL_MEMORY_DINO_PATH"):
            raise VisualMemoryUnavailable(
                "Set VISUAL_MEMORY_DINO_PATH to a local DINOv2-small model"
            )
        weights = next(
            (
                path / name
                for name in ("model.safetensors", "pytorch_model.bin")
                if (path / name).is_file()
            ),
            None,
        )
        if (
            weights is None
            or not (path / "config.json").is_file()
            or not (path / "preprocessor_config.json").is_file()
        ):
            raise VisualMemoryUnavailable("DINOv2 model files are missing")
        try:
            import PIL
            import torch
            import transformers
            from transformers import AutoImageProcessor, AutoModel

            torch.set_num_threads(1)
            processor = AutoImageProcessor.from_pretrained(
                str(path), local_files_only=True, use_fast=False
            )
            model = AutoModel.from_pretrained(str(path), local_files_only=True).to("cpu").eval()
            if model.config.model_type != "dinov2" or model.config.hidden_size != 384:
                raise ValueError("Expected a 384-dimensional DINOv2-small model")
            config = {
                "dimensions": 384,
                "model": "facebook/dinov2-small",
                "pooling": "cls",
                "normalization": "l2",
                "weights_sha256": _digest(weights),
                "model_config_sha256": _digest(path / "config.json"),
                "preprocessor_sha256": _digest(path / "preprocessor_config.json"),
                "transformers_version": transformers.__version__,
                "torch_version": str(torch.__version__),
                "pillow_version": PIL.__version__,
            }
            fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
            config["fingerprint"] = fingerprint
            self._config = config
            self._processor, self._model = processor, model
        except Exception as exc:
            raise VisualMemoryUnavailable(f"Could not load DINOv2 model: {exc}") from exc

    def embed(self, image: Image.Image) -> EmbeddingResult:
        with self._lock:
            self._load_embedder()
            try:
                import torch

                inputs = self._processor(images=image.convert("RGB"), return_tensors="pt")
                with torch.inference_mode():
                    vector = self._model(**inputs).last_hidden_state[0, 0].float()
                    norm = torch.linalg.vector_norm(vector)
                    if not torch.isfinite(vector).all() or not torch.isfinite(norm) or norm <= 0:
                        raise ValueError("Invalid embedding")
                    vector = vector / norm
                return EmbeddingResult(
                    "dinov2-small:cls:" + self._config["fingerprint"],
                    vector.tolist(),
                    dict(self._config),
                )
            except Exception as exc:
                raise VisualMemoryUnavailable(f"DINOv2 inference failed: {exc}") from exc

    def _load_detector(self) -> None:
        if self._session is not None:
            return
        path = os.environ.get("VISUAL_MEMORY_DETECTOR_PATH")
        if not path or not Path(path).is_file():
            raise VisualMemoryUnavailable(
                "Set VISUAL_MEMORY_DETECTOR_PATH to the local character.onnx"
            )
        try:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                path, sess_options=options, providers=["CPUExecutionProvider"]
            )
            if session.get_inputs()[0].shape != [1, 3, 640, 640] or session.get_outputs()[
                0
            ].shape != [1, 8400, 6]:
                raise ValueError("Unexpected detector input or output shape")
            self._session = session
        except Exception as exc:
            raise VisualMemoryUnavailable(f"Could not load YOLOX detector: {exc}") from exc

    def detect(self, image: Image.Image) -> list[dict[str, Any]]:
        # Upstream: ksasao/anime-character-detector python/onnx_predictor.py.
        with self._lock:
            self._load_detector()
            try:
                import cv2
                import numpy as np

                width, height = image.size
                ratio = min(640 / width, 640 / height)
                bgr = np.asarray(image.convert("RGB"))[:, :, ::-1]
                resized = cv2.resize(
                    bgr,
                    (max(1, int(width * ratio)), max(1, int(height * ratio))),
                    interpolation=cv2.INTER_CUBIC,
                )
                padded = np.full((640, 640, 3), 114, dtype=np.uint8)
                padded[: resized.shape[0], : resized.shape[1]] = resized
                blob = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)[None]
                output = self._session.run(None, {self._session.get_inputs()[0].name: blob})[0][0]
                if output.shape != (8400, 6):
                    raise ValueError("Unexpected detector output")
                grids, strides = [], []
                for stride in (8, 16, 32):
                    x, y = np.meshgrid(np.arange(640 // stride), np.arange(640 // stride))
                    grids.append(np.stack((x, y), axis=-1).reshape(-1, 2))
                    strides.append(np.full((x.size, 1), stride))
                grid, stride = np.concatenate(grids), np.concatenate(strides)
                centers = (output[:, :2] + grid) * stride
                sizes = np.exp(output[:, 2:4]) * stride
                scores = output[:, 4] * output[:, 5]
                mask = (
                    np.isfinite(scores)
                    & np.isfinite(sizes).all(axis=1)
                    & np.isfinite(centers).all(axis=1)
                    & (scores > 0.3)
                )
                boxes = (
                    np.concatenate((centers - sizes / 2, centers + sizes / 2), axis=1)[mask] / ratio
                )
                scores = scores[mask]
                # Upstream NMS uses inclusive-coordinate areas before clipping.
                order = scores.argsort()[::-1]
                result = []
                while order.size:
                    i = order[0]
                    box = boxes[i]
                    clipped = [
                        max(0.0, min(float(box[0]), width)),
                        max(0.0, min(float(box[1]), height)),
                        max(0.0, min(float(box[2]), width)),
                        max(0.0, min(float(box[3]), height)),
                    ]
                    if clipped[0] < clipped[2] and clipped[1] < clipped[3]:
                        result.append(
                            {"bbox": clipped, "score": float(scores[i]), "label": "character"}
                        )
                    rest = order[1:]
                    intersection = np.maximum(
                        0,
                        np.minimum(box[2:], boxes[rest, 2:])
                        - np.maximum(box[:2], boxes[rest, :2])
                        + 1,
                    ).prod(axis=1)
                    area = np.maximum(0, box[2:] - box[:2] + 1).prod()
                    other_area = np.maximum(0, boxes[rest, 2:] - boxes[rest, :2] + 1).prod(axis=1)
                    order = rest[intersection / (area + other_area - intersection) <= 0.45]
                return result
            except Exception as exc:
                raise VisualMemoryUnavailable(f"YOLOX inference failed: {exc}") from exc


@lru_cache(maxsize=1)
def get_engine() -> VisualMemoryEngine:
    return VisualMemoryEngine()
