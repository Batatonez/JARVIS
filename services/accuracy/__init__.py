"""Camada de precisão do JARVIS — decide COMO responder antes de responder.

Motivada por um bug real: perguntado sobre "el ninho", o JARVIS afirmou com
confiança que era um chocolate da Nestlé. O modelo não reconheceu o termo e
preencheu a lacuna.

A regra central é arquitetural, não de prompt: uma resposta errada dita com
confiança é pior que "preciso verificar isso", e o código precisa tornar isso
verdade — ver `docs/ACCURACY_AND_VERIFICATION.md`.
"""
