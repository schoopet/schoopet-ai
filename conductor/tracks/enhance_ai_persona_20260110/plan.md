# Plan: Enhance AI Persona and Conciseness

## Phase 1: Baseline & Analysis
- [x] Task: Analyze current system prompts in `agents/shoopet/root_agent.py` and related files to identify areas for improvement.
- [~] Task: Create a reproduction test script `agents/shoopet/evals/test_persona_baseline.py` that sends standard inputs and logs the response length and content.
- [ ] Task: Run the baseline test and record the current metrics (length, tone).
- [ ] Task: Conductor - User Manual Verification 'Phase 1: Baseline & Analysis' (Protocol in workflow.md)

## Phase 2: Implementation
- [ ] Task: Update `agents/shoopet/root_agent.py` system instructions to strictly enforce conciseness (max ~2 sentences per thought) and the "sidekick" persona.
- [ ] Task: Update `agents/shoopet/search_agent.py` instructions to ensure search summaries are brief and actionable.
- [ ] Task: Conductor - User Manual Verification 'Phase 2: Implementation' (Protocol in workflow.md)

## Phase 3: Verification & Refinement
- [ ] Task: Run the `agents/shoopet/evals/test_persona_baseline.py` script again to verify the improvements.
- [ ] Task: Add a specific unit test `agents/shoopet/tests/test_persona.py` that asserts response length constraints.
- [ ] Task: Refine the prompts if the tone is too robotic or too whimsical (iterative tuning).
- [ ] Task: Conductor - User Manual Verification 'Phase 3: Verification & Refinement' (Protocol in workflow.md)
