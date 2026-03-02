#!/usr/bin/env python3
"""
inference.py — Starter inference script for UI-TARS-1.5-7B

Usage:
    python hpc/inference.py --image screenshot.png --task "click the search bar"
    python hpc/inference.py --image screenshot.png --task "type hello world" --quantize 4bit
"""

import argparse
import sys
from pathlib import Path


SYSTEM_PROMPT = (
    "You are a GUI agent. You are given a screenshot and a task. "
    "You need to output your thought and the action to perform."
)

MODEL_ID = "ByteDance-Seed/UI-TARS-1.5-7B"


def load_model(model_id: str, quantize: str):
    """Load UI-TARS model and processor."""
    kwargs = {
        "device_map": "auto",
        "torch_dtype": "auto",
        "trust_remote_code": True,
    }

    if quantize == "4bit":
        from transformers import BitsAndBytesConfig
        import torch

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
        )
    elif quantize == "8bit":
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)

    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor

    print(f"Loading model: {model_id} (quantize={quantize})...")
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_id, **kwargs)
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    print("Model loaded.")
    return model, processor


def run_inference(model, processor, image_path: str, task: str):
    """Run a single inference pass."""
    from PIL import Image
    from qwen_vl_utils import process_vision_info

    image = Image.open(image_path).convert("RGB")
    orig_width, orig_height = image.size

    # Build chat messages in Qwen2.5-VL format
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image_path},
                {"type": "text", "text": task},
            ],
        },
    ]

    # Prepare inputs
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(model.device)

    # Generate
    output_ids = model.generate(**inputs, max_new_tokens=256)

    # Decode only the new tokens
    generated_ids = output_ids[:, inputs.input_ids.shape[1]:]
    response = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()

    return response, orig_width, orig_height


def parse_response(response: str, width: int, height: int):
    """Parse model response into structured actions and PyAutoGUI code."""
    from ui_tars.action_parser import (
        parse_action_to_structure_output,
        parsing_response_to_pyautogui_code,
        smart_resize,
    )

    resized_h, resized_w = smart_resize(height, width)

    parsed = parse_action_to_structure_output(
        response,
        factor=1000,
        origin_resized_height=resized_h,
        origin_resized_width=resized_w,
        model_type="qwen25vl",
    )

    pyautogui_code = parsing_response_to_pyautogui_code(
        responses=parsed,
        image_height=height,
        image_width=width,
    )

    return parsed, pyautogui_code


def main():
    parser = argparse.ArgumentParser(description="UI-TARS-1.5-7B inference")
    parser.add_argument("--image", required=True, help="Path to screenshot image")
    parser.add_argument("--task", required=True, help="Task instruction (e.g. 'click the search bar')")
    parser.add_argument("--model", default=MODEL_ID, help=f"HuggingFace model ID (default: {MODEL_ID})")
    parser.add_argument(
        "--quantize",
        choices=["none", "4bit", "8bit"],
        default="none",
        help="Quantization mode for VRAM flexibility (default: none)",
    )
    args = parser.parse_args()

    # Validate image
    if not Path(args.image).is_file():
        print(f"Error: image not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    # Load model
    model, processor = load_model(args.model, args.quantize)

    # Run inference
    response, width, height = run_inference(model, processor, args.image, args.task)

    print("\n" + "=" * 60)
    print("  Raw Model Response")
    print("=" * 60)
    print(response)

    # Parse response
    parsed, pyautogui_code = parse_response(response, width, height)

    print("\n" + "=" * 60)
    print("  Parsed Actions")
    print("=" * 60)
    for i, action in enumerate(parsed):
        print(f"\n--- Action {i+1} ---")
        if action.get("thought"):
            print(f"  Thought: {action['thought']}")
        print(f"  Type:    {action.get('action_type', 'N/A')}")
        print(f"  Inputs:  {action.get('action_inputs', {})}")

    print("\n" + "=" * 60)
    print("  PyAutoGUI Code")
    print("=" * 60)
    print(pyautogui_code)


if __name__ == "__main__":
    main()
