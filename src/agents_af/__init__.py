"""
HR Policy Knowledge Base Lab - Agent Framework Pipeline (agents_af)

Alternative agent implementation using Microsoft Agent Framework with
FoundryChatClient for model inference and SequentialBuilder for
orchestration.

This module mirrors the agents in ``src/agents/`` but uses the
Agent Framework pattern (agent-framework-foundry) instead of the
azure-ai-projects SDK directly.

Architecture:
    FoundryChatClient  →  Agent Framework Agents  →  SequentialBuilder
        ↓
    Azure AI Search (Agentic Retrieval)  →  Copilot Studio

Agents:
  1. RetrievalAgent            – Agentic retrieval (KnowledgeBaseRetrievalClient),
                                  fallback: hybrid search (text + vector + semantic)
  2. SourceValidatorAgent      – FoundryChatClient Agent: provenance check + trust reasoning
  3. ReferenceValidatorAgent   – FoundryChatClient Agent: citation extraction + grounding
  4. AnswerSynthesisAgent      – FoundryChatClient Agent: answer synthesis from grounded sources

Orchestrated by SequentialBuilder mixing agents with custom Executors.
"""
