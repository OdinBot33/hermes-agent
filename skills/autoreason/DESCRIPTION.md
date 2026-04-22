# autoreason

Phase 4 skill — generic adversarial content/decision sharpening loop.

Wraps `aquaguard.autoreason.loop.run()` so any AGM decision surface can
run the loop with a surface-specific knowledge layer + judge graph +
voice rubric.

## Signature

```python
from aquaguard.autoreason.loop import run as autoreason_run

result = autoreason_run(
    target_url="…",
    target_keyword="…",
    use_fixtures=False,
    judge_graph="agm-board-members",   # or agm-pool-techs, agm-suburban-hoa-residents, agm-applicants
    target_audience="HOA board treasurer",
)
```

## When to invoke

- Any AGM decision with >=2 viable options where MiroFish swarm can
  arbitrate (per feedback_mirofish_mandatory).
- Any content artifact where voice consistency matters (per
  feedback_agm_blog_voice).
- Any publishing surface where Mahuki approval or Chris/Patricia
  sign-off gate is desirable.

## When NOT to invoke

- Trivial decisions (one obvious correct answer).
- Customer-data-adjacent surfaces that would leak HOA names or dollar
  amounts (per feedback_agm_customer_data_hands_off).
- Chris's personal email replies (per feedback_chris_invisible_operation).
- Logo or visual design (per feedback_never_touch_logo,
  feedback_agm_visual_redesign).

## See also

- Master spec: `aquaguard/docs/mbm/MBM-AGM-AUTOREASON-001.md`
- Loop implementation: `aquaguard/autoreason/`
- Slack gateway handler: `_external/hermes-agent/gateway/handlers/autoreason.py`
- LaunchAgent plists: `aquaguard/autoreason/launchd/`
