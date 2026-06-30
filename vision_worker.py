# PrivilegePod — private VISION endpoint on Runpod Flash (the 2nd stage).
#
# Reads SCANNED / photographed evidence that has no text layer — the $48,200
# photographed invoice, the approval/denial portal screenshots, struck receipts
# — using an open-source vision-language model (Qwen2.5-VL) on YOUR Runpod GPU.
# Its text output feeds the LLM stage, making PrivilegePod a multi-endpoint
# pipeline: ingest -> vision (scans) + llm (text) -> synthesis. Nothing leaves
# your infrastructure.
#
#   input:  {image_b64, prompt}   ->   {text, model, runtime}
from runpod_flash import Endpoint, GpuType


@Endpoint(
    name="privilege_vision",
    gpu=[GpuType.NVIDIA_GEFORCE_RTX_4090],
    workers=(0, 2),
    dependencies=["torch", "torchvision", "transformers>=4.49",
                  "accelerate", "qwen-vl-utils", "pillow"],
    idle_timeout=300,
    execution_timeout_ms=0,
    env={"HF_HUB_ENABLE_HF_TRANSFER": "1"},
)
class PrivilegeVision:
    MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"

    def __init__(self):
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        self.processor = AutoProcessor.from_pretrained(self.MODEL_ID)
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.MODEL_ID, torch_dtype=torch.bfloat16
        ).to("cuda")

    async def read(self, input_data: dict) -> dict:
        """input: {image_b64, prompt?}  ->  {text, model, runtime}"""
        import base64
        import io
        import platform

        from PIL import Image
        from qwen_vl_utils import process_vision_info

        img = Image.open(io.BytesIO(base64.b64decode(input_data["image_b64"]))).convert("RGB")
        prompt = input_data.get(
            "prompt",
            "Transcribe the text and key fields (numbers, dates, status) from this document image.",
        )
        messages = [{"role": "user", "content": [
            {"type": "image", "image": img},
            {"type": "text", "text": prompt},
        ]}]
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self.processor(
            text=[text], images=image_inputs, padding=True, return_tensors="pt"
        ).to("cuda")
        generated = self.model.generate(**inputs, max_new_tokens=512)
        trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, generated)]
        out_text = self.processor.batch_decode(
            trimmed, skip_special_tokens=True)[0].strip()

        runtime = {"host": platform.node(), "model": self.MODEL_ID}
        try:
            import torch

            runtime["gpu"] = torch.cuda.get_device_name(0)
        except Exception:
            pass
        return {"text": out_text, "model": self.MODEL_ID, "runtime": runtime}
