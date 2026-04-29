"""
HR Policy Knowledge Base Lab - Multi-Agent Pipeline

The pipeline uses Azure AI Search **agentic retrieval** as the primary
retrieval mechanism: a Knowledge Source + Knowledge Base handle query
planning, subquery decomposition, semantic ranking, and answer synthesis.
Falls back to deterministic hybrid search when agentic retrieval is
unavailable.

Post-retrieval validation agents are still orchestrated via Agent
Framework SequentialBuilder.

Agents:
  1. RetrievalAgent            – Agentic retrieval (KnowledgeBaseRetrievalClient),
                                  fallback: hybrid search (text + vector + semantic)
  2. SourceValidatorAgent      – Foundry Agent: provenance check + trust reasoning
  3. ReferenceValidatorAgent   – Foundry Agent: citation extraction + grounding assessment
  4. AnswerSynthesisAgent      – Uses agentic retrieval answer when available;
                                  fallback: Foundry Agent (GPT-4.1) synthesis
"""
