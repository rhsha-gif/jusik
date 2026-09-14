---
name: status-sync
description: "Update QuantPilot's living status board docs/STATUS.md after a stage, gate, or mission task completes — refresh the stage table, add a dated \"최근 완료\" entry with exact verification evidence, and keep detailed rationale in a separate docs/*_report.md. Use this skill whenever work on QuantPilot finishes and needs recording, whenever the user says \"STATUS 갱신\", \"현황판 업데이트\", \"완료 보고서\", \"상태 문서에 반영\", or a mainline integration / gate completion needs documenting. STATUS.md has strict conventions (overwrite-style living doc, evidence format, no live-readiness claims) that this skill encodes."
---

# status-sync

Update QuantPilot's living status board docs/STATUS.md after a stage, gate, or mission task completes — refresh the stage table, add a dated "최근 완료" entry with exact verification evidence, and keep detailed rationale in a separate docs/*_report.md. Use this skill whenever work on QuantPilot finishes and needs recording, whenever the user says "STATUS 갱신", "현황판 업데이트", "완료 보고서", "상태 문서에 반영", or a mainline integration / gate completion needs documenting. STATUS.md has strict conventions (overwrite-style living doc, evidence format, no live-readiness claims) that this skill encodes.

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [status-sync] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:status-sync; mode=bridge; edit .agents/aorch/definitions.json -->
