from abc import ABC, abstractmethod
import streamlit as st
from google import genai
from google.genai import types
from openai import OpenAI as OpenAIClient


# Abstract Base Class
class AIStrategy(ABC):
    @abstractmethod
    def generate_stream(self, model_id, chat_history, system_instruction):
        """Must yield strings (tokens)"""
        pass


# Concrete Strategy for Google Gemini
class GeminiStrategy(AIStrategy):
    def generate_stream(self, model_id, chat_history, system_instruction):
        client = genai.Client(api_key=st.secrets["api_keys"]["google"])
        api_contents = [
            types.Content(
                role="user" if m["role"] == "user" else "model",
                parts=[types.Part.from_text(text=m["content"])]
            ) for m in chat_history
        ]

        # Use models.generate_content_stream for Gemini
        for chunk in client.models.generate_content_stream(
                model=model_id,
                contents=api_contents,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    system_instruction=system_instruction
                )
        ):
            if chunk.text:
                yield chunk.text


# Concrete Strategy for OpenAI
class OpenAIStrategy(AIStrategy):
    def generate_stream(self, model_id, chat_history, system_instruction):
        oa_client = OpenAIClient(api_key=st.secrets["api_keys"]["openai"])
        oa_messages = [{"role": "system", "content": system_instruction}]
        for m in chat_history:
            role = "assistant" if m["role"] == "model" else m["role"]
            oa_messages.append({"role": role, "content": m["content"]})

        # Set stream=True for OpenAI
        response = oa_client.chat.completions.create(
            model=model_id,
            messages=oa_messages,
            temperature=1,
            stream=True
        )
        for chunk in response:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AIManager:
    def __init__(self, model_label):
        self.failover_order = [
            model_label,
            "ChatGPT 5.2" if model_label == "gemini-3-pro-preview" else "gemini-3-pro-preview"
        ]
        self.strategies = {
            "gemini-3-pro-preview": (GeminiStrategy(), "gemini-3-pro-preview"),
            "ChatGPT 5.2": (OpenAIStrategy(), "gpt-5")
        }

    def get_response_stream(self, chat_history, system_instruction):
        for label in self.failover_order:
            try:
                strategy, model_id = self.strategies[label]
                # Test if the generator works
                for chunk in strategy.generate_stream(model_id, chat_history, system_instruction):
                    yield chunk, label  # Yielding the TUPLE
                return
            except Exception as e:
                if label == self.failover_order[-1]:
                    yield f"Error: {str(e)}", label
                continue
