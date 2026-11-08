"""Compare offline DINO preprocessing/pooling on reviewed, source-held-out images.

Input: JSON references with id, image_url or path, source_group/source_sha256,
quality and links[{identity_id,name}]; optional queries[{path,identity_id,name}].
This diagnostic never writes labels, embeddings or model settings to a database.
"""

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


def source_key(reference):
    return reference.get("source_group") or reference.get("source_sha256") or str(reference["id"])


def ranking(similarities, references, excluded_source=None, legacy=False):
    groups = {}
    for similarity, reference in zip(similarities, references, strict=True):
        if excluded_source is not None and source_key(reference) == excluded_source:
            continue
        for link in reference["links"]:
            groups.setdefault(link["identity_id"], []).append(
                (float(similarity), reference, link))
    result = []
    for identity, matches in groups.items():
        matches.sort(key=lambda row: (-row[0], row[1]["id"]))
        independent = {}
        for match in matches:
            independent.setdefault(source_key(match[1]), match)
        evidence = list(independent.values())[:5]
        best, reference, link = evidence[0]
        if legacy:
            scores = [row[0] for row in evidence]
            score = (.65 * best + .25 * np.mean(scores) + .1 *
                     max(0, 1 - max(scores) + min(scores)))
            score *= .5 + .5 * max(row[1].get("quality", 1)
                                   for row in evidence)
        else:
            score, best, reference, link = max(
                ((similarity * (.5 + .5 * ref.get("quality", 1)), similarity, ref, label)
                 for similarity, ref, label in evidence), key=lambda row: (row[0], -row[2]["id"]))
        result.append({"identity_id": identity, "name": link.get("name", str(identity)),
                       "score": float(score), "similarity": best, "reference_id": reference["id"]})
    return sorted(result, key=lambda row: (-row["score"], row["identity_id"]))


def evaluate(vectors, references, queries, *, legacy=False, min_similarity=0.5, min_margin=0.04):
    reference_vectors = vectors[:len(references)]
    correct = top3 = evaluated = skipped = 0
    accepted = accepted_correct = absent_false_candidates = 0

    def candidate(predictions):
        return bool(predictions and predictions[0]["similarity"] >= min_similarity and
                    (len(predictions) == 1 or predictions[0]["score"] - predictions[1]["score"] >= min_margin))
    for index, reference in enumerate(references):
        expected = {link["identity_id"] for link in reference["links"]}
        predictions = ranking(reference_vectors @ reference_vectors[index], references,
                              source_key(reference), legacy)
        other_indices = [i for i, row in enumerate(references)
                         if not expected.intersection(link["identity_id"] for link in row["links"])]
        absent = ranking(reference_vectors[other_indices] @ reference_vectors[index],
                         [references[i] for i in other_indices], legacy=legacy)
        absent_false_candidates += candidate(absent)
        if not any(row["identity_id"] in expected for row in predictions):
            skipped += 1
            continue
        evaluated += 1
        correct += predictions[0]["identity_id"] in expected
        top3 += any(row["identity_id"] in expected for row in predictions[:3])
        if candidate(predictions):
            accepted += 1
            accepted_correct += predictions[0]["identity_id"] in expected
    holdouts = []
    for index, query in enumerate(queries, start=len(references)):
        predictions = ranking(reference_vectors @
                              vectors[index], references, legacy=legacy)
        rank = next((i + 1 for i, row in enumerate(predictions)
                    if row["identity_id"] == query["identity_id"]), None)
        holdouts.append({"name": query["name"], "expected_identity": query["identity_id"],
                         "expected_rank": rank, "candidate": candidate(predictions), "top5": predictions[:5]})
    return {"evaluated": evaluated, "unrepresented_after_source_exclusion": skipped,
            "top1": correct, "top3": top3,
            "top1_rate": correct / evaluated if evaluated else None,
            "accepted": accepted, "accepted_correct": accepted_correct,
            "absent_identity_false_candidates": absent_false_candidates, "absent_identity_queries": len(references),
            "policy": {"min_similarity": min_similarity, "min_margin": min_margin}, "holdouts": holdouts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument(
        "--backend", choices=("dinov2", "ccip"), default="dinov2")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    references = manifest["references"]
    queries = manifest.get("queries", [])
    args.cache.mkdir(parents=True, exist_ok=True)
    paths = []
    for reference in references:
        if reference.get("path"):
            paths.append(Path(reference["path"]))
            continue
        url = reference["image_url"]
        if not url.startswith("https://"):
            raise ValueError(
                "Reference downloads require explicit HTTPS image URLs")
        path = args.cache / \
            (hashlib.sha256(url.encode()).hexdigest() + ".image")
        if not path.exists():
            request = urllib.request.Request(
                url, headers={"User-Agent": "SolarisAtlas-VisualBenchmark/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("Reference image exceeds 8 MiB")
            path.write_bytes(data)
        paths.append(path)
    paths.extend(Path(query["path"]) for query in queries)
    if args.backend == "ccip":
        import os

        from wuwa_story.visual_memory.inference import VisualMemoryEngine

        os.environ["VISUAL_MEMORY_CCIP_PATH"] = str(args.model_path.resolve())
        engine = VisualMemoryEngine(backend="ccip")
        started = time.monotonic()
        embeddings = []
        for path in paths:
            with Image.open(path) as source:
                result = engine.embed(
                    ImageOps.exif_transpose(source).convert("RGB"))
            embeddings.append(result.vector)
        vectors = np.asarray(embeddings, dtype=np.float32)
        metrics = evaluate(vectors, references, queries,
                           min_similarity=result.config["retrieval_min_similarity"],
                           min_margin=result.config["retrieval_min_margin"])
        metrics["seconds"] = round(time.monotonic() - started, 3)
        np.save(args.cache / "ccip-production.npy", vectors)
        report = {"references": len(references), "queries": len(queries),
                  "model_key": result.model_key, "variants": {"ccip": metrics}}
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        return
    import torch
    from transformers import AutoImageProcessor, AutoModel

    torch.set_num_threads(2)
    processor = AutoImageProcessor.from_pretrained(
        args.model_path, local_files_only=True, use_fast=False)
    model = AutoModel.from_pretrained(
        args.model_path, local_files_only=True).eval()
    report = {"references": len(references), "queries": len(
        queries), "variants": {}}
    for mode in ("center", "fit", "stretch"):
        started = time.monotonic()
        cls_vectors, patch_vectors = [], []
        for path in paths:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
            if mode == "fit":
                image = ImageOps.pad(
                    image, (224, 224), method=Image.Resampling.BICUBIC, color=(124, 116, 104))
            elif mode == "stretch":
                image = image.resize((224, 224), Image.Resampling.BICUBIC)
            inputs = processor(images=image, return_tensors="pt", **(
                {} if mode == "center" else {"do_resize": False, "do_center_crop": False}))
            with torch.inference_mode():
                output = model(**inputs).last_hidden_state[0]
            for vector, collection in ((output[0], cls_vectors), (output[1:].mean(0), patch_vectors)):
                collection.append((vector / vector.norm()).numpy())
        for pooling, values in (("cls", cls_vectors), ("patch_mean", patch_vectors)):
            vectors = np.stack(values)
            np.save(args.cache / f"{mode}-{pooling}.npy", vectors)
            metrics = evaluate(vectors, references, queries)
            metrics["seconds"] = round(time.monotonic() - started, 3)
            report["variants"][f"{mode}_{pooling}"] = metrics
            if mode == "center" and pooling == "cls":
                report["variants"]["production_legacy"] = evaluate(
                    vectors, references, queries, legacy=True)
        print(json.dumps({"mode": mode, "seconds": round(
            time.monotonic() - started, 3)}), flush=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: {"top1": row["top1"], "total": row["evaluated"],
                            "holdout_ranks": [x["expected_rank"] for x in row["holdouts"]]}
                      for key, row in report["variants"].items()}), flush=True)


if __name__ == "__main__":
    main()
