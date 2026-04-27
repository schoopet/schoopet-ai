import os
import logging

import vertexai
from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService

def get_memory_service():
    """
    Returns the Vertex AI Memory Bank Service.
    Requires GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION to be set.
    """
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    agent_engine_id = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID")

    if not project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set for Vertex AI Memory.")
    if not agent_engine_id:
        raise ValueError("GOOGLE_CLOUD_AGENT_ENGINE_ID environment variable must be set for Vertex AI Memory Bank.")

    print(f"Using Vertex AI Memory for project: {project_id}, engine: {agent_engine_id}")
    return VertexAiMemoryBankService(
        project=project_id, 
        location=location,
        agent_engine_id=agent_engine_id
    )

async def save_session_to_memory(session_service, memory_service, app_name, user_id, session_id):
    """
    Helper function to manually save the current session to the memory bank.
    Useful for ensuring persistence upon session termination.
    """
    print("Saving session to memory...")
    try:
        current_session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        if current_session:
            await memory_service.add_session_to_memory(current_session)
            print("Session saved to memory successfully.")
        else:
            print("Error: Could not retrieve session to save.")
    except Exception as e:
        print(f"Failed to save memory: {e}")

def get_memory_bank_config(project_id: str = None, location: str = None):
    """
    Returns the Native Memory Bank configuration dictionary.
    Includes memory topics for user preferences, personal info, and facts.
    """

    project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Return a dictionary configuration.
    # Use the structure expected by the API (usually dicts are accepted).
    memory_bank_config = {
        "generation_config": {
            "model": f"projects/{project_id}/locations/{location}/publishers/google/models/gemini-2.5-flash"
        },
        "customization_configs": [
            {
                "memory_topics": [
                    {"managed_memory_topic": {"managed_topic_enum": "USER_PERSONAL_INFO"}},
                    {"managed_memory_topic": {"managed_topic_enum": "USER_PREFERENCES"}},
                    {"managed_memory_topic": {"managed_topic_enum": "KEY_CONVERSATION_DETAILS"}},
                    {"managed_memory_topic": {"managed_topic_enum": "EXPLICIT_INSTRUCTIONS"}},
                    {"custom_memory_topic": {
                        "label": "social_memories",
                        "description": """Critical information for supporting social interactions and relationships. Extract and store:

PEOPLE: Names with disambiguating context (relationship, workplace, activity group), preferences, dislikes, dietary restrictions, allergies, sensitivities, interests, hobbies, personality traits, communication preferences.

EVENTS & PLANS: Scheduled events with dates/times, recurring commitments, social gatherings, meetings, important deadlines, date conflicts.

COMMITMENTS & INTENTIONS: Promises made, gifts to buy, follow-ups needed, things to remember to do or say.

CONTEXT & CONNECTIONS: Why someone likes/dislikes something, how people know each other, relevant history, social dynamics, past conflicts or positive interactions.

Always capture WHO, WHAT, WHEN, WHY, and HOW information relates to other stored facts. This memory supports someone who may have difficulty recalling social details."""
                    }},
                    {"custom_memory_topic": {
                        "label": "conversation_summaries",
                        "description": """Narrative summaries of prior conversations so the assistant can pick up where it left off when the user returns after a gap.

For each meaningful conversation produce ONE summary fact that captures:
- WHEN: rough timeframe (today, yesterday morning, last week)
- TOPIC: what the conversation was about
- OUTCOME / OPEN THREAD: decisions made, tasks created or scheduled, questions left unanswered, follow-ups the user is expecting

Format the summary as a single dense sentence or two — enough that re-reading it makes the prior session feel continuous. Do NOT generate summaries for trivial exchanges (greetings only, single-question lookups with no follow-up). Do NOT duplicate facts already captured by the other topics — this is for narrative continuity, not atomic facts."""
                    }}
                ],
                "generate_memories_examples": [
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "How's everything going?"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "My friend Sarah from work loves hiking and is planning a trip to Yosemite next month. She's been talking about it non-stop!"}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": [
                            {"fact": "Sarah (friend from work) loves hiking."},
                            {"fact": "Sarah (friend from work) is planning a trip to Yosemite next month."}
                        ]
                    },
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "Tell me about your weekend plans!"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "I'm meeting my sister Sarah for brunch. She's a vegetarian and allergic to peanuts, so we're going to that new plant-based place downtown."}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": [
                            {"fact": "Sarah (sister) is a vegetarian."},
                            {"fact": "Sarah (sister) is allergic to peanuts."}
                        ]
                    },
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "Any gift ideas you're thinking about?"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "Yeah, my colleague Mike's birthday is next week. He's really into vintage vinyl records, especially jazz. I'm thinking of getting him a Miles Davis album."}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": [
                            {"fact": "Mike (colleague) has a birthday coming up next week."},
                            {"fact": "Mike (colleague) is into vintage vinyl records, especially jazz."},
                            {"fact": "Mike (colleague) would appreciate a Miles Davis album as a gift."}
                        ]
                    },
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "How was your day?"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "Good! Just grabbed coffee with Alex from the gym. Then I called my brother Alex to wish him luck on his interview."}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": [
                            {"fact": "Alex (from the gym) is someone the user gets coffee with."},
                            {"fact": "Alex (brother) had an interview recently."}
                        ]
                    },
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "What's new?"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "Just saying hi, nothing much happening today."}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": []
                    },
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "How can I help you today?"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "dinner with Lisa Friday 7pm. Italian place on Main St"}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": [
                            {"fact": "Dinner scheduled with Lisa on Friday at 7pm."},
                            {"fact": "Dinner location: Italian restaurant on Main St."}
                        ]
                    },
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "Tell me what you learned!"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "Found out Rachel is gluten-free and doesn't like spicy food. Also she gets anxious in crowded places."}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": [
                            {"fact": "Rachel is gluten-free."},
                            {"fact": "Rachel doesn't like spicy food."},
                            {"fact": "Rachel gets anxious in crowded places."}
                        ]
                    },
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "Anything to remember?"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "Need to get birthday gift for Mom. She mentioned wanting a new cookbook. Birthday is March 15th."}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": [
                            {"fact": "Need to buy birthday gift for Mom (mother)."},
                            {"fact": "Mom (mother) wants a new cookbook as a gift."},
                            {"fact": "Mom's (mother) birthday is March 15th."}
                        ]
                    },
                    {
                        "conversation_source": {
                            "events": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": "What's the plan?"}]
                                    }
                                },
                                {
                                    "content": {
                                        "role": "user",
                                        "parts": [{"text": "Meeting Tom's parents for first time. He said they're traditional, prefer formal settings, his dad doesn't drink alcohol."}]
                                    }
                                }
                            ]
                        },
                        "generated_memories": [
                            {"fact": "Tom's (partner/boyfriend) parents are traditional and prefer formal settings."},
                            {"fact": "Tom's (partner/boyfriend) father doesn't drink alcohol."},
                            {"fact": "Meeting Tom's (partner/boyfriend) parents for the first time (upcoming event)."}
                        ]
                    }
                ]
            }
        ]
    }

    
    return memory_bank_config
