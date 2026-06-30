# PrivilegePod — private structured-LLM endpoint on Runpod Flash.
#
# Serves an open-source instruct model (Qwen2.5) on YOUR Runpod GPU and returns
# JSON conforming to a caller-supplied schema — the private, in-house
# replacement for Anthropic's `messages.parse(output_format=PydanticModel)`.
# Nothing leaves your infra.
#
# Uses torch + transformers (pip's torch wheel matches the worker's CUDA, which
# `gpu_worker` already proved runs cleanly on Flash). Structured output is via
# strict JSON-schema prompting + defensive parsing; the caller (AuditRouter's
# FlashProvider) validates into Pydantic and falls back on miss.
#
# Class-based (stateful) worker: __init__ loads the model ONCE per warm worker;
# infer() reuses it. Flash recompiles each unit from source in isolation, so
# every import and helper lives INSIDE the class.
from runpod_flash import Endpoint, GpuType


@Endpoint(
    name="privilege_llm",
    gpu=[GpuType.NVIDIA_GEFORCE_RTX_4090, GpuType.NVIDIA_RTX_A5000],
    workers=(0, 2),
    dependencies=["torch", "transformers>=4.45", "accelerate"],
    idle_timeout=300,           # keep warm 5 min between calls (avoid reload)
    execution_timeout_ms=0,     # no per-call cap (extraction can run long)
    env={"HF_HUB_ENABLE_HF_TRANSFER": "1"},
)
class PrivilegeLLM:
    # 3B keeps cold-starts fast while proving the loop; bump to 7B/14B for the
    # full reconciliation quality. Apache-2.0, strong at structured output.
    MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"

    def __init__(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL_ID)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.MODEL_ID, torch_dtype=torch.bfloat16
        ).to("cuda")

    async def infer(self, input_data: dict) -> dict:
        """input: {system, user, schema?, max_tokens?}  ->
        {json, raw, model, _runpod_proof}"""
        import json
        import platform

        system = input_data.get("system", "")
        user = input_data.get("user", "")
        schema = input_data.get("schema")
        max_tokens = int(input_data.get("max_tokens", 1024))

        sys_msg = system
        if schema:
            sys_msg = (
                f"{system}\n\nRespond with ONLY a single JSON object that conforms "
                f"to this JSON Schema. No prose, no markdown fences.\n"
                f"SCHEMA:\n{json.dumps(schema)}"
            )

        text = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": sys_msg},
             {"role": "user", "content": user}],
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self.tokenizer([text], return_tensors="pt").to("cuda")
        generated = self.model.generate(
            **inputs, max_new_tokens=max_tokens, do_sample=False
        )
        new_tokens = generated[0][inputs.input_ids.shape[1]:]
        raw = self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

        parsed = None
        if schema:
            s = raw
            if "```" in s:  # strip markdown fences if the model added them
                s = s.replace("```json", "").replace("```", "").strip()
            start, end = s.find("{"), s.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(s[start:end + 1])
                except json.JSONDecodeError:
                    parsed = None

        proof = {"host": platform.node(), "os": platform.platform(),
                 "model": self.MODEL_ID}
        try:
            import torch

            proof["gpu"] = torch.cuda.get_device_name(0)
            proof["cuda"] = torch.version.cuda
        except Exception:
            pass

        return {"json": parsed, "raw": raw, "model": self.MODEL_ID,
                "_runpod_proof": proof}
