# Character retrieval accuracy, 2026-11-08

The production ranking averaged unrelated poses and penalized identities with more
diverse references. It also hid the runner-up before deciding ambiguity when
`limit=1`. Retrieval now uses the best quality-weighted independent reference and
computes ambiguity before truncating the response. Extra references cannot reduce
the score. Similarity remains an uncalibrated comparison, not a probability.

## Evaluation

The evaluation snapshot contains 84 human-approved references. Each reference is
queried with its entire source video/screenshot excluded from the gallery. There
are 66 queries with another source representing their identity; 18 cannot be
evaluated for identification because no independent reference remains. Labels are
the current human annotations, not an independently audited ground truth.

| Method | Correct first candidate / 66 |
| --- | ---: |
| Production DINOv2 with averaged ranking | 16 |
| DINOv2 with corrected nearest-reference ranking | 22 |
| DINOv2 full-image padding, CLS | 16 |
| DINOv2 stretched full image, CLS | 26 |
| CCIP character embeddings, corrected ranking | 39 |

CCIP placed the correct identity within the first three candidates for 61/66
queries. On three additional, unindexed Hsin images (two illustrations and one
game profile screenshot), Hsin was the first candidate for all three. These three
examples motivated the experiment, so they are regression examples rather than
an unbiased accuracy estimate.

The conservative CCIP candidate policy requires cosine similarity >= 0.85 and a
gap >= 0.04 from the next identity. It accepted only 5/66 source-held-out queries,
all correctly in this snapshot. Removing each query's identity entirely from the
gallery produced no accepted candidates in 84 trials. These exploratory results
do not establish a production precision guarantee; coverage is deliberately low.

| Additional example | Hsin similarity | Result |
| --- | ---: | --- |
| Portrait illustration | 0.844 | Unknown; golden version is close at 0.827 |
| Full illustration | 0.920 | Candidate |
| Game profile screenshot | 0.891 | Candidate |
| Right detector region of game screenshot | 0.898 | Candidate |
| Left detector region of game screenshot | Below policy threshold | Unknown |

Detector proposals are regions, not a count of distinct characters. Do not merge
overlapping proposals indiscriminately: different characters can overlap too.

## Model and reproducibility

CCIP is opt-in with `VISUAL_MEMORY_EMBEDDER=ccip` and a local
`VISUAL_MEMORY_CCIP_PATH` directory containing `model_feat.onnx`. DINOv2 remains
the default and remains available for generic visual experiments.

The model comes from [DeepGHS CCIP ONNX](https://huggingface.co/deepghs/ccip_onnx),
revision `eb2acdd29af1703388d3d0c04221add322bc9110`, variant
`ccip-caformer-24-randaug-pruned`. Feature model SHA-256:
`4ea118d16496274f4f6e08d3afc768cc592389e8f7f32f8732ce2215c228ac5f`.
Preprocessing follows the [upstream implementation](https://dghs-imgutils.deepghs.org/main/_modules/imgutils/metrics/ccip.html):
RGB, full-image bilinear resize to 384x384, CLIP channel normalization. Normalized
768-dimensional features use cosine retrieval. The upstream metric model was
checked against cosine: its distance is `(1 - cosine) / 2`, with equivalent ordering
apart from floating-point ties. The upstream threshold is not substituted for the
more conservative local policy above.

The model key fingerprints the actual weights, preprocessing, retrieval policy
and runtime versions. Inference never downloads weights or executes remote model
code. ONNX Runtime uses the CPU provider and one intra/inter-op thread.

`scripts/benchmark-visual-retrieval.py` consumes a JSON manifest:

```json
{
  "references": [{"id": 1, "path": "reference.png", "source_group": "video:A",
    "quality": 1.0, "links": [{"identity_id": 1, "name": "Example"}]}],
  "queries": [{"path": "unindexed-query.png", "identity_id": 1, "name": "held-out example"}]
}
```

References can instead supply an explicit HTTPS `image_url`; downloaded crops are
cached locally. Source groups must identify the original video or screenshot,
not individual exported frames. Run with the server source on `PYTHONPATH`:

```sh
python scripts/benchmark-visual-retrieval.py --backend ccip --manifest manifest.json \
  --model-path /models/ccip --cache /tmp/visual-evaluation --output results.json
```

The default `--backend dinov2` compares six preprocessing/pooling variants and
the previous ranking. Evaluation performs no database writes.

## Rollout and rollback

Stage verified weights first. With a new API image and an explicit CCIP environment
override, reindex retained references using the existing `reindex` service in short
transactions. This adds a separate model's vectors; it does not relabel references,
change approvals, delete DINOv2 vectors or repeat cutscene extraction.

Activate CCIP only after checking reference coverage. Persist the backend and path
in production variables and installed API environment. Gracefully replace the API
and dedicated visual-memory worker; preserve unrelated media/import workers.
Queued scans retain their original backend and exact fingerprint checks, so an old
DINOv2 scan can finish after activation. New model fingerprints create separate
scan plans and never overwrite existing human review.

Rollback can keep this code and set `VISUAL_MEMORY_EMBEDDER=dinov2`, then gracefully
replace the API and visual worker. Existing vectors for both models remain intact.
Do not roll back to code without CCIP support while CCIP scans are queued.
