# Specification: Enhance AI Persona and Conciseness

## Context
The current AI agent behavior may be too verbose or generic. The new product guidelines define Schoopet as a "Whimsical Sidekick" that communicates in a "Concise & Direct" manner suitable for SMS.

## Goals
1.  **Conciseness:** Reduce response length to be optimized for SMS (avoiding long paragraphs).
2.  **Persona:** Infuse a "friendly, magical sidekick" tone without being cheesy or overbearing.
3.  **Directness:** Ensure the AI gets to the point quickly while remaining supportive.

## Requirements
-   **System Prompts:** Update the system instructions in `agents/shoopet/root_agent.py` (and others if necessary) to reflect the new guidelines.
-   **Testing:** Create a test case that compares "Before" and "After" responses to common queries (e.g., "Remind me to buy milk").
-   **Constraint:** Responses should ideally stay under 160-320 characters when possible, or be broken into clear, short segments.

## User Stories
-   As a user, I want to receive short SMS replies so I don't have to scroll through walls of text.
-   As a user with ADHD, I want the AI to be encouraging but not distracting.
