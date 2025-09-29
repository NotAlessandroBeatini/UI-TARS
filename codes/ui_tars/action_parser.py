# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: Apache-2.0
import re
import json
import ast
import math
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Iterable
from enum import Enum, unique
from PIL import Image, ImageDraw
import jsonschema
from jsonschema import validate

IMAGE_FACTOR = 28
MIN_PIXELS = 100 * 28 * 28
MAX_PIXELS = 16384 * 28 * 28
MAX_RATIO = 200

KEY_MAPPING = {
    "point": "start_box",
    "start_point": "start_box",
    "end_point": "end_box"
}

FN_REGEX_PATTERN = r'<function=([^>]+)>\n?(.*?)</function>'
FN_PARAM_REGEX_PATTERN = r'<parameter=([^>]+)>(.*?)</parameter>'

PARAM_INT_TYPE = [
    "integer",
]

PARAM_NUMBER_TYPE = [
    "number",
]

PARAM_STRING_TYPE = [
    "string",
    "str"
]

PARAM_ARRAY_TYPE = [
    "array",
    "list"
]

PARAM_BOOL_TYPE = [
    "boolean",
    "bool",
]

PARAM_OBJECT_TYPE = [
    "object",
]

class FunctionCallConversionError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class FunctionCallValidationError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message

class SessionEndedError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message

@unique
class GUIActionType(Enum):
    CLICK = 'click'
    LEFT_DOUBLE = 'left_double'
    RIGHT_SINGLE = 'right_single'
    SCROLL = 'scroll'
    DRAG = 'drag'

    MOUSE_DOWN = 'mouse_down'
    MOUSE_UP = 'mouse_up'
    MOVE_TO = 'move_to'

    HOTKEY = 'hotkey'
    TYPE = 'type'
    PRESS = 'press'
    RELEASE = 'release'

    WAIT = 'wait'
    FINISHED = 'finished'
    CALL_USER = 'call_user'

    OPEN_COMPUTER = 'open_computer'


@dataclass
class GUIAction:
    action_type: GUIActionType
    custom_data: dict

    def to_action_str(self) -> str:
        # 返回一个函数，输入图片，输出action_str
        action_str = f'{self.action_type.value}('
        if 'start_box' in self.custom_data:
            action_str += f"start_box='{self.custom_data['start_box']}'"
        if 'end_box' in self.custom_data:
            if not action_str.endswith('('):
                action_str += ', '
            action_str += f"end_box='{self.custom_data['end_box']}'"
        for key, value in self.custom_data.items():
            if key in ['start_box', 'end_box']:
                continue
            if not action_str.endswith('('):
                action_str += ', '
            action_str += f"{key}='{value}'"
        action_str += ')'
        return action_str

    def to_json(self, data_json_format: bool = False) -> dict:
        """转换成yining数据处理的格式
        data_json_format: 是否使用旧的格式
        """
        if data_json_format:
            # custom和boxes两个字段
            action_instance = {
                'type': self.action_type.value,
                'custom': {},
                'boxes': [],
            }
            if 'start_box' in self.custom_data:
                start_box = self.custom_data['start_box']
                if len(start_box) == 2:
                    start_box = start_box + start_box
                action_instance['boxes'].append(start_box)
            if 'end_box' in self.custom_data:
                end_box = self.custom_data['end_box']
                if len(end_box) == 2:
                    end_box = end_box + end_box
                action_instance['boxes'].append(end_box)
            for key, value in self.custom_data.items():
                if key in ['start_box', 'end_box']:
                    continue
                action_instance['custom'][key] = value
            return action_instance
        return {
            'type': self.action_type.value,
        } | self.custom_data

    def draw_img(self, img: Image.Image) -> Image.Image:
        """
        把这个action画到图片上。 如果action包含点坐标，就画出来点
        """
        draw = ImageDraw.Draw(img)
        if 'start_box' in self.custom_data:
            start_box = self.custom_data['start_box']  # 这里是相对坐标 0-1之间, 画一个圆点
            draw.ellipse(
                (
                    int(start_box[0] * img.width - 3),
                    int(start_box[1] * img.height - 3),
                    int(start_box[0] * img.width + 3),
                    int(start_box[1] * img.height + 3),
                ),
                'red',
            )
        if 'end_box' in self.custom_data:
            end_box = self.custom_data['end_box']  # 这里是相对坐标 0-1之间, 画一个圆点
            draw.ellipse(
                (
                    int(end_box[0] * img.width - 3),
                    int(end_box[1] * img.height - 3),
                    int(end_box[0] * img.width + 3),
                    int(end_box[1] * img.height + 3),
                ),
                'blue',
            )
        return img

    @staticmethod
    def from_json(action_json):
        new_action = GUIAction(
            action_type=GUIActionType(action_json['type']),
            custom_data={},
        )
        for key in action_json['custom']:
            new_action.custom_data[key] = action_json['custom'][key]
        if len(action_json['boxes']) > 0:
            new_action.custom_data['start_box'] = action_json['boxes'][0][:2]
        if len(action_json['boxes']) > 1:
            new_action.custom_data['end_box'] = action_json['boxes'][1][:2]
        return new_action

    def action_the_same(self, other_action) -> bool:
        if self.action_type != other_action.action_type:
            return False
        if set(self.custom_data.keys()) != set(other_action.custom_data.keys()):
            return False
        for key in self.custom_data:
            if key not in ['start_box', 'end_box']:
                if self.custom_data[key] != other_action.custom_data[key]:
                    return False
            else:
                # 对于点坐标，有0.02的宽容度
                abs_coor = math.sqrt(
                    (self.custom_data[key][0] - other_action.custom_data[key][0]) ** 2
                    + (self.custom_data[key][1] - other_action.custom_data[key][1]) ** 2
                )
                if abs_coor > 0.02:
                    return False

        # # 最后一道关卡，如果是scroll action，不算stuck
        # if self.action_type == GUIActionType.SCROLL:
        #     return False
        # else:
        #     return True
        return True
    
def convert_point_to_coordinates(text, is_answer=False):
    # 匹配 <bbox> 后面的四个数字
    pattern = r"<point>(\d+)\s+(\d+)</point>"

    def replace_match(match):
        x1, y1 = map(int, match.groups())
        x = (x1 + x1) // 2  # 使用截断取整
        y = (y1 + y1) // 2  # 使用截断取整
        if is_answer:
            return f"({x},{y})"  # 只返回 (x, y) 格式
        return f"({x},{y})"  # 返回带标签的格式

    # 去掉 [EOS] 并替换 <bbox> 坐标
    text = re.sub(r"\[EOS\]", "", text)
    return re.sub(pattern, replace_match, text).strip()

def escape_single_quotes(text):
    # 匹配未转义的单引号（不匹配 \\'）
    pattern = r"(?<!\\)'"
    return re.sub(pattern, r"\\'", text)


def round_by_factor(number: int, factor: int) -> int:
    """Returns the closest integer to 'number' that is divisible by 'factor'."""
    return round(number / factor) * factor


def ceil_by_factor(number: int, factor: int) -> int:
    """Returns the smallest integer greater than or equal to 'number' that is divisible by 'factor'."""
    return math.ceil(number / factor) * factor


def floor_by_factor(number: int, factor: int) -> int:
    """Returns the largest integer less than or equal to 'number' that is divisible by 'factor'."""
    return math.floor(number / factor) * factor


def linear_resize(height: int,
                  width: int,
                  factor: int = IMAGE_FACTOR,
                  min_pixels: int = MIN_PIXELS,
                  max_pixels: int = MAX_PIXELS) -> tuple[int, int]:
    if width * height > max_pixels:
        """
        如果图片超过/低于像素限制，则计算一个缩放因子resize_factor，使图片的像素数缩小到等于或小于max_pixels。这个缩放因子是通过开平方根计算的，确保纵横比保持不变,这样原始的相对坐标可以不经转换直接复用
        """
        resize_factor = math.sqrt(max_pixels / (width * height))
        width, height = int(width * resize_factor), int(height * resize_factor)
    if width * height < min_pixels:
        resize_factor = math.sqrt(min_pixels / (width * height))
        width, height = math.ceil(width * resize_factor), math.ceil(
            height * resize_factor)

    return height, width


def smart_resize(height: int,
                 width: int,
                 factor: int = IMAGE_FACTOR,
                 min_pixels: int = MIN_PIXELS,
                 max_pixels: int = MAX_PIXELS) -> tuple[int, int]:
    """
    Rescales the image so that the following conditions are met:

    1. Both dimensions (height and width) are divisible by 'factor'.

    2. The total number of pixels is within the range ['min_pixels', 'max_pixels'].

    3. The aspect ratio of the image is maintained as closely as possible.
    """
    if max(height, width) / min(height, width) > MAX_RATIO:
        raise ValueError(
            f"absolute aspect ratio must be smaller than {MAX_RATIO}, got {max(height, width) / min(height, width)}"
        )
    h_bar = max(factor, round_by_factor(height, factor))
    w_bar = max(factor, round_by_factor(width, factor))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(height / beta, factor)
        w_bar = floor_by_factor(width / beta, factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(height * beta, factor)
        w_bar = ceil_by_factor(width * beta, factor)
    return h_bar, w_bar


def parse_action_to_structure_output(text,
                                     factor,
                                     origin_resized_height,
                                     origin_resized_width,
                                     model_type="qwen25vl",
                                     max_pixels=16384 * 28 * 28,
                                     min_pixels=100 * 28 * 28):
    text = text.strip()

    if "<point>" in text:
        text = convert_point_to_coordinates(text)
    if "start_point=" in text:
        text = text.replace("start_point=", "start_box=")
    if "end_point=" in text:
        text = text.replace("end_point=", "end_box=")
    if "point=" in text:
        text = text.replace("point=", "start_box=")

    if model_type == "qwen25vl":
        smart_resize_height, smart_resize_width = smart_resize(
            origin_resized_height,
            origin_resized_width,
            factor=IMAGE_FACTOR,
            min_pixels=min_pixels,
            max_pixels=max_pixels)

    # 正则表达式匹配 Action 字符串
    if text.startswith("Thought:"):
        thought_pattern = r"Thought: (.+?)(?=\s*Action: |$)"
        thought_hint = "Thought: "
    elif text.startswith("Reflection:"):
        thought_pattern = r"Reflection: (.+?)Action_Summary: (.+?)(?=\s*Action: |$)"
        thought_hint = "Reflection: "
    elif text.startswith("Action_Summary:"):
        thought_pattern = r"Action_Summary: (.+?)(?=\s*Action: |$)"
        thought_hint = "Action_Summary: "
    else:
        thought_pattern = r"Thought: (.+?)(?=\s*Action: |$)"
        thought_hint = "Thought: "
    reflection, thought = None, None
    thought_match = re.search(thought_pattern, text, re.DOTALL)
    if thought_match:
        if len(thought_match.groups()) == 1:
            thought = thought_match.group(1).strip()
        elif len(thought_match.groups()) == 2:
            thought = thought_match.group(2).strip()
            reflection = thought_match.group(1).strip()
    assert "Action:" in text
    action_str = text.split("Action: ")[-1]

    tmp_all_action = action_str.split(")\n\n")
    all_action = []
    for action_str in tmp_all_action:
        if "type(content" in action_str:
            if not action_str.strip().endswith(")"):
                action_str = action_str.strip() + ")"
            # 正则表达式匹配 content 中的字符串并转义单引号
            def escape_quotes(match):
                content = match.group(1)  # 获取 content 的值
                return content

            # 使用正则表达式进行替换
            pattern = r"type\(content='(.*?)'\)"  # 匹配 type(content='...')
            if re.search(pattern, action_str):  # 检查是否有匹配项
                content = re.sub(pattern, escape_quotes, action_str)
            else:
                raise ValueError("Pattern not found in the input string.")

            # 处理字符串
            action_str = escape_single_quotes(content)
            action_str = "type(content='" + action_str + "')"
        if not action_str.strip().endswith(")"):
            action_str = action_str.strip() + ")"
        all_action.append(action_str)

    parsed_actions = [
        ast_parse(action.replace("\n", "\\n").lstrip())
        for action in all_action
    ]
    actions = []
    for action_instance, raw_str in zip(parsed_actions, all_action):
        if action_instance == None:
            print(f"Action can't parse: {raw_str}")
            raise ValueError(f"Action can't parse: {raw_str}")
        action_type = action_instance["function"]
        params = action_instance["args"]

        # import pdb; pdb.set_trace()
        action_inputs = {}
        for param_name, param in params.items():
            if param == "": continue
            param = param.lstrip()  # 去掉引号和多余的空格
            # 处理start_box或者end_box参数格式 '<bbox>x1 y1 x2 y2</bbox>'
            action_inputs[param_name.strip()] = param

            if "start_box" in param_name or "end_box" in param_name:
                ori_box = param
                # Remove parentheses and split the string by commas
                numbers = ori_box.replace("(", "").replace(")", "").split(",")

                # Convert to float and scale by 1000
                # Qwen2.5vl output absolute coordinates, qwen2vl output relative coordinates
                if model_type == "qwen25vl":
                    float_numbers = []
                    for num_idx, num in enumerate(numbers):
                        num = float(num)
                        if (num_idx + 1) % 2 == 0:
                            float_numbers.append(
                                float(num / smart_resize_height))
                        else:
                            float_numbers.append(
                                float(num / smart_resize_width))
                else:
                    float_numbers = [float(num) / factor for num in numbers]

                if len(float_numbers) == 2:
                    float_numbers = [
                        float_numbers[0], float_numbers[1], float_numbers[0],
                        float_numbers[1]
                    ]
                action_inputs[param_name.strip()] = str(float_numbers)

        # import pdb; pdb.set_trace()
        actions.append({
            "reflection": reflection,
            "thought": thought,
            "action_type": action_type,
            "action_inputs": action_inputs,
            "text": text
        })
    return actions


def parsing_response_to_pyautogui_code(responses,
                                       image_height: int,
                                       image_width: int,
                                       input_swap: bool = True,
                                       scale_factor: int=1000) -> str:
    '''
    将M模型的输出解析为OSWorld中的action，生成pyautogui代码字符串
    参数:
        response: 包含模型输出的字典，结构类似于：
        {
            "action_type": "hotkey",
            "action_inputs": {
                "hotkey": "v ctrl",
                "start_box": None,
                "end_box": None
            }
        }
    返回:
        生成的pyautogui代码字符串
    '''

    pyautogui_code = f"import pyautogui\nimport time\n"
    if isinstance(responses, dict):
        responses = [responses]
    for response_id, response in enumerate(responses):
        if "observation" in response:
            observation = response["observation"]
        else:
            observation = ""

        if "thought" in response:
            thought = response["thought"]
        else:
            thought = ""

        if response_id == 0:
            pyautogui_code += f"'''\nObservation:\n{observation}\n\nThought:\n{thought}\n'''\n"
        else:
            pyautogui_code += f"\ntime.sleep(1)\n"

        action_dict = response
        action_type = action_dict.get("action_type")
        action_inputs = action_dict.get("action_inputs", {})
        old_action_inputs = deepcopy(action_inputs)
        # 遍历 action_inputs 并替换键名
        action_inputs = {}
        for key_name, value in old_action_inputs.items():
            # 如果键名在映射关系中，则替换为新的键名
            new_key_name = KEY_MAPPING.get(key_name, key_name)
            action_inputs[new_key_name] = value
            if "<point>" in value or "<start_point>" in value:
                value = eval(convert_point_to_coordinates(value))
                action_inputs[new_key_name] = value

        if action_type == "hotkey":
            # Parsing hotkey action
            if "key" in action_inputs:
                hotkey = action_inputs.get("key", "")
            else:
                hotkey = action_inputs.get("hotkey", "")

            if hotkey == "arrowleft":
                hotkey = "left"

            elif hotkey == "arrowright":
                hotkey = "right"

            elif hotkey == "arrowup":
                hotkey = "up"

            elif hotkey == "arrowdown":
                hotkey = "down"

            if hotkey:
                # Handle other hotkeys
                keys = hotkey.split()  # Split the keys by space
                convert_keys = []
                for key in keys:
                    if key == "space":
                        key = ' '
                    convert_keys.append(key)
                pyautogui_code += f"\npyautogui.hotkey({', '.join([repr(k) for k in convert_keys])})"

        elif action_type in ["press", "keydown"]:
            # Parsing press action
            if "key" in action_inputs:
                key_to_press = action_inputs.get("key", "")
            else:
                key_to_press = action_inputs.get("press", "")

            if key_to_press == "arrowleft":
                key_to_press = "left"

            elif key_to_press == "arrowright":
                key_to_press = "right"

            elif key_to_press == "arrowup":
                key_to_press = "up"

            elif key_to_press == "arrowdown":
                key_to_press = "down"

            elif key_to_press == "space":
                key_to_press = " "

            if key_to_press:
                # Simulate pressing a single key
                pyautogui_code += f"\npyautogui.keyDown({repr(key_to_press)})"

        elif action_type in ["release", "keyup"]:
            # Parsing press action
            if "key" in action_inputs:
                key_to_press = action_inputs.get("key", "")
            else:
                key_to_press = action_inputs.get("press", "")

            if key_to_press == "arrowleft":
                key_to_press = "left"

            elif key_to_press == "arrowright":
                key_to_press = "right"

            elif key_to_press == "arrowup":
                key_to_press = "up"

            elif key_to_press == "arrowdown":
                key_to_press = "down"

            elif key_to_press == "space":
                key_to_press = " "

            if key_to_press:
                # Simulate pressing a single key
                pyautogui_code += f"\npyautogui.keyUp({repr(key_to_press)})"

        elif action_type == "type":
            # Parsing typing action using clipboard
            content = action_inputs.get("content", "")
            content = escape_single_quotes(content)
            stripped_content = content
            if content.endswith("\n") or content.endswith("\\n"):
                stripped_content = stripped_content.rstrip("\\n").rstrip("\n")
            if content:
                if input_swap:
                    pyautogui_code += f"\nimport pyperclip"
                    pyautogui_code += f"\npyperclip.copy('{stripped_content}')"
                    pyautogui_code += f"\npyautogui.hotkey('ctrl', 'v')"
                    pyautogui_code += f"\ntime.sleep(0.5)\n"
                    if content.endswith("\n") or content.endswith("\\n"):
                        pyautogui_code += f"\npyautogui.press('enter')"
                else:
                    pyautogui_code += f"\npyautogui.write('{stripped_content}', interval=0.1)"
                    pyautogui_code += f"\ntime.sleep(0.5)\n"
                    if content.endswith("\n") or content.endswith("\\n"):
                        pyautogui_code += f"\npyautogui.press('enter')"

        elif action_type in ["drag", "select"]:
            # Parsing drag or select action based on start and end_boxes
            start_box = action_inputs.get("start_box")
            end_box = action_inputs.get("end_box")
            if start_box and end_box:
                try:
                    # Assuming start_box is a string representation of a list or tuple, e.g., "[x1, y1, x2, y2]"
                    if isinstance(start_box, str):
                        start_box = eval(start_box)  # Use eval cautiously; ensure input is sanitized
                except Exception as e:
                    raise ValueError(f"Point format error: {start_box}. Error: {e}")

                # Validate the format of start_box
                if not isinstance(start_box, (tuple, list)):
                    raise ValueError(f"Point format error: {start_box}. Expected a tuple or list.")

                if len(start_box) == 4:
                    # Extract coordinates if the box has 4 elements
                    x1, y1, x2, y2 = start_box
                elif len(start_box) == 2:
                    # Handle case where only two points are provided (e.g., [x1, y1])
                    x1, y1 = start_box
                    x2, y2 = x1, y1  # Default x2, y2 to x1, y1 if not provided
                else:
                    raise ValueError(f"Point format error: {start_box}. Expected 2 or 4 elements.")
                
                sx = round(float((x1 + x2) / 2) * image_width / scale_factor, 3)
                sy = round(float((y1 + y2) / 2) * image_height / scale_factor, 3)
                
                try:
                    # Assuming end_box is a string representation of a list or tuple, e.g., "[x1, y1, x2, y2]"
                    if isinstance(end_box, str):
                        end_box = eval(end_box)  # Use eval cautiously; ensure input is sanitized
                except Exception as e:
                    raise ValueError(f"Point format error: {end_box}. Error: {e}")

                # Validate the format of end_box
                if not isinstance(end_box, (tuple, list)):
                    raise ValueError(f"Point format error: {end_box}. Expected a tuple or list.")

                if len(end_box) == 4:
                    # Extract coordinates if the box has 4 elements
                    x1, y1, x2, y2 = end_box
                elif len(end_box) == 2:
                    # Handle case where only two points are provided (e.g., [x1, y1])
                    x1, y1 = end_box
                    x2, y2 = x1, y1  # Default x2, y2 to x1, y1 if not provided
                else:
                    raise ValueError(f"Point format error: {end_box}. Expected 2 or 4 elements.")
                
                ex = round(float((x1 + x2) / 2) * image_width / scale_factor, 3)
                ey = round(float((y1 + y2) / 2) * image_height / scale_factor, 3)
                pyautogui_code += (
                    f"\npyautogui.moveTo({sx}, {sy})\n"
                    f"\npyautogui.dragTo({ex}, {ey}, duration=1.0)\n")

        elif action_type == "scroll":
            # Parsing scroll action
            start_box = action_inputs.get("start_box")
            if start_box:
                try:
                    # Assuming start_box is a string representation of a list or tuple, e.g., "[x1, y1, x2, y2]"
                    if isinstance(start_box, str):
                        start_box = eval(start_box)  # Use eval cautiously; ensure input is sanitized
                except Exception as e:
                    raise ValueError(f"Point format error: {start_box}. Error: {e}")

                # Validate the format of start_box
                if not isinstance(start_box, (tuple, list)):
                    raise ValueError(f"Point format error: {start_box}. Expected a tuple or list.")

                if len(start_box) == 4:
                    # Extract coordinates if the box has 4 elements
                    x = start_box[0]
                    y = start_box[1]
                elif len(start_box) == 2:
                    # Handle case where only two points are provided (e.g., [x1, y1])
                    x, y = start_box
                else:
                    raise ValueError(f"Point format error: {start_box}. Expected 2 or 4 elements.")
            else:
                x = None
                y = None
            direction = action_inputs.get("direction", "")

            if x == None:
                if "up" in direction.lower():
                    pyautogui_code += f"\npyautogui.scroll(5)"
                elif "down" in direction.lower():
                    pyautogui_code += f"\npyautogui.scroll(-5)"
            else:
                if "up" in direction.lower():
                    pyautogui_code += f"\npyautogui.scroll(5, x={x}, y={y})"
                elif "down" in direction.lower():
                    pyautogui_code += f"\npyautogui.scroll(-5, x={x}, y={y})"

        elif action_type in [
                "click", "left_single", "left_double", "right_single", "hover"
        ]:
            # Parsing mouse click actions
            start_box = action_inputs.get("start_box")
            start_box = str(start_box)
            if start_box:
                try:
                    # Assuming start_box is a string representation of a list or tuple, e.g., "[x1, y1, x2, y2]"
                    if isinstance(start_box, str):
                        start_box = eval(start_box)  # Use eval cautiously; ensure input is sanitized
                except Exception as e:
                    raise ValueError(f"Point format error: {start_box}. Error: {e}")

                # Validate the format of start_box
                if not isinstance(start_box, (tuple, list)):
                    raise ValueError(f"Point format error: {start_box}. Expected a tuple or list.")

                if len(start_box) == 4:
                    # Extract coordinates if the box has 4 elements
                    x1, y1, x2, y2 = start_box
                elif len(start_box) == 2:
                    # Handle case where only two points are provided (e.g., [x1, y1])
                    x1, y1 = start_box
                    x2, y2 = x1, y1  # Default x2, y2 to x1, y1 if not provided
                else:
                    raise ValueError(f"Point format error: {start_box}. Expected 2 or 4 elements.")
                
                x = round(float((x1 + x2) / 2) * image_width / scale_factor, 3)
                y = round(float((y1 + y2) / 2) * image_height / scale_factor, 3)
                if action_type == "left_single" or action_type == "click":
                    pyautogui_code += f"\npyautogui.click({x}, {y}, button='left')"
                elif action_type == "left_double":
                    pyautogui_code += f"\npyautogui.doubleClick({x}, {y}, button='left')"
                elif action_type == "right_single":
                    pyautogui_code += f"\npyautogui.click({x}, {y}, button='right')"
                elif action_type == "hover":
                    pyautogui_code += f"\npyautogui.moveTo({x}, {y})"

        elif action_type in ["finished"]:
            pyautogui_code = f"DONE"

        else:
            pyautogui_code += f"\n# Unrecognized action type: {action_type}"

    return pyautogui_code


def add_box_token(input_string):
    # Step 1: Split the string into individual actions
    if "Action: " in input_string and "start_box=" in input_string:
        suffix = input_string.split("Action: ")[0] + "Action: "
        actions = input_string.split("Action: ")[1:]
        processed_actions = []
        for action in actions:
            action = action.strip()
            # Step 2: Extract coordinates (start_box or end_box) using regex
            coordinates = re.findall(
                r"(start_box|end_box)='\((\d+),\s*(\d+)\)'", action)

            updated_action = action  # Start with the original action
            for coord_type, x, y in coordinates:
                # Convert x and y to integers
                updated_action = updated_action.replace(
                    f"{coord_type}='({x},{y})'",
                    f"{coord_type}='<|box_start|>({x},{y})<|box_end|>'")
            processed_actions.append(updated_action)

        # Step 5: Reconstruct the final string
        final_string = suffix + "\n\n".join(processed_actions)
    else:
        final_string = input_string
    return final_string

def _extract_and_validate_params(matching_tool: dict, param_matches: Iterable[re.Match], fn_name: str) -> dict:
    params = {}
    # Parse and validate parameters
    required_params = set()
    if 'parameters' in matching_tool and 'required' in matching_tool['parameters']:
        required_params = set(matching_tool['parameters'].get('required', []))

    allowed_params = set()
    if 'parameters' in matching_tool and 'properties' in matching_tool['parameters']:
        allowed_params = set(matching_tool['parameters']['properties'].keys())

    param_name_to_type = {}
    if 'parameters' in matching_tool and 'properties' in matching_tool['parameters']:
        param_name_to_type = {
            name: val.get('type', 'string') for name, val in matching_tool['parameters']['properties'].items()
        }

    # Collect parameters
    found_params = set()
    for param_match in param_matches:
        param_name = param_match.group(1)
        param_value = param_match.group(2)
        # Validate parameter is allowed
        if param_name not in allowed_params:
            raise FunctionCallValidationError(
                f"Parameter '{param_name}' is not allowed for function '{fn_name}'. "
                f'Allowed parameters: {allowed_params}'
            )

        # Validate and convert parameter type
        # supported: string, integer, array
        if param_name in param_name_to_type:
            if param_name_to_type[param_name].lower() in PARAM_INT_TYPE:
                try:
                    param_value = int(param_value)
                except ValueError as e:
                    raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an integer.") from e
            elif param_name_to_type[param_name].lower() in PARAM_ARRAY_TYPE:
                try:
                    param_value = json.loads(param_value)
                except json.JSONDecodeError as e:
                    raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an array.") from e
            elif param_name_to_type[param_name].lower() in PARAM_NUMBER_TYPE:
                try:
                    param_value = float(param_value)
                except ValueError as e:
                    raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an float number.") from e
            elif param_name_to_type[param_name].lower() in PARAM_STRING_TYPE:
                try:
                    param_value = str(param_value)
                except ValueError as e:
                    raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an string.") from e
            elif param_name_to_type[param_name].lower() in PARAM_BOOL_TYPE:
                try:
                    if param_name_to_type[param_name].lower() == "true":
                        param_value = True
                    elif param_name_to_type[param_name].lower() == "false":
                        param_value = False
                    else:
                        param_value = bool(param_value)
                except ValueError as e:
                    raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an boolean.") from e
            elif param_name_to_type[param_name].lower() in PARAM_OBJECT_TYPE:
                try:
                    param_value = json.loads(param_value)
                except json.JSONDecodeError as e:
                    raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an json string object.") from e
            else:
                # string
                pass

        # Enum check
        if (
            'enum' in matching_tool['parameters']['properties'][param_name]
            and param_value not in matching_tool['parameters']['properties'][param_name]['enum']
        ):
            raise FunctionCallValidationError(
                f"Parameter '{param_name}' is expected to be one of {matching_tool['parameters']['properties'][param_name]['enum']}."
            )

        params[param_name] = param_value
        found_params.add(param_name)

    # Check all required parameters are present
    missing_params = required_params - found_params
    if missing_params:
        raise FunctionCallValidationError(f"Missing required parameters for function '{fn_name}': {missing_params}")
    return params

def remove_nest_function(tool_schemas):
    new_schemas = []
    for tool_schema in tool_schemas:
        if "function" in tool_schema:
            tool_schema = tool_schema["function"]
            tool_schema = {
                'type': 'function',  # 首先插入 'type' 字段
                **tool_schema        # 然后插入原有的其他字段
            }
        new_schemas.append(tool_schema)
    return new_schemas

def parse_xml_action(content: str, tool_schemas: list, think_end_token: str = "</think_never_used_51bce0c785ca2f68081bfa7d91973934>") -> list:
    """
    Parse function-style tool calls from the response content.

    Args:
        content (str): 
            The XML-like string containing one or more `<function>` blocks. 
            Each `<function>` block should follow this format:
            ```
            <function=function_name>
                <parameter=parameter_name>parameter_value</parameter>
            </function>
            ```
            Example:
            ```
            <function=click>
                <parameter=point>100 200</parameter>
            </function>
            <function=type>
                <parameter=content>123</parameter>
            </function>
            ```

        tool_schemas (list of dict): 
            A list of tool schema definitions. Each schema should be a dictionary 
            with the following structure:
            ```
            {
                "function": {
                    "name": "function_name",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "parameter_name": {
                                "type": "str",
                                "description": "Description of the parameter."
                            }
                        },
                        "required": ["parameter_name"]
                    }
                }
            }
            ```

    Returns:
        list of dict: 
            A list of parsed tool calls. Each tool call is represented as a dictionary 
            with the following structure:
            ```
            {
                "function": "function_name",
                "parameters": {
                    "parameter_name": "parameter_value"
                }
            }
            ```

    Raises:
        FunctionCallValidationError: 
            If a function name in the content does not match any tool schema.
    """
    tool_calls = []
    tool_schemas = remove_nest_function(tool_schemas)
    # Find all function calls using regex pattern
    fn_matches = re.finditer(FN_REGEX_PATTERN, content, re.DOTALL)

    for fn_match in fn_matches:
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)

        # Find matching tool
        matching_schema = None
        for tool_schema in tool_schemas:
            if tool_schema["name"] == fn_name:
                matching_schema = tool_schema
                break

        if not matching_schema:
            raise FunctionCallValidationError(
                f"Function '{fn_name}' not found in available tools: {[tool['name'] for tool in tool_schemas]}"
            )

        # Parse parameters
        param_matches = re.finditer(FN_PARAM_REGEX_PATTERN, fn_body, re.DOTALL)

        # Extract and validate parameters using the existing function
        if matching_schema["type"] == "function":
            params = _extract_and_validate_params(matching_schema, param_matches, fn_name)
        else:
            params = {}

        # Create tool call
        tool_calls.append({"function": fn_name, "parameters": params})

    return tool_calls

def parse_structure_to_tree(input_text):
    # Step 1: 提取所有标签信息
    def extract_tags(text):
        # 定义正则表达式模式
        tag_pattern = r"<(/?)(\w+)(?:=([^>]+))?>"
        # 定义需要提取的特定标签
        allowed_tags = {"parameter", "item", "object"}
        tags = []

        # 使用正则表达式查找所有标签
        for match in re.finditer(tag_pattern, text):
            is_closing = match.group(1) == "/"  # 是否是关闭标签
            tag_name = match.group(2)          # 标签名
            tag_param = match.group(3)         # 标签参数（如果有）
            start_idx = match.start()          # 标签起始位置
            end_idx = match.end()              # 标签结束位置

            # 仅处理特定的标签
            if tag_name in allowed_tags:
                tags.append({
                    "is_closing": is_closing,
                    "tag_name": tag_name,
                    "tag_param": tag_param,
                    "start_idx": start_idx,
                    "end_idx": end_idx
                })

        return tags

    # Step 2: 构建树结构
    def build_tree(tags, text):
        """
        构建树结构，包含开始标签和闭合标签。
        :param tags: 提取的标签列表。
        :param text: 原始文本，用于提取内容。
        :return: 构建的树结构。
        """
        # 引入一个虚拟根节点
        root = {
            "tag_name": "root",
            "tag_param": None,
            "start_idx": None,
            "end_idx": None,
            "children": []
        }
        stack = [root]  # 用于管理当前的标签层级，初始栈中包含虚拟根节点

        for tag in tags:
            if not tag["is_closing"]:
                # 开始标签
                node = {
                    "tag_name": tag["tag_name"],
                    "tag_param": tag["tag_param"],
                    "start_idx": tag["start_idx"],
                    "end_idx": tag["end_idx"],
                    "children": []
                }
                # 将当前节点添加到栈顶节点的 children 中
                stack[-1]["children"].append(node)
                # 将当前节点压入栈
                stack.append(node)
            else:
                # 闭合标签
                if stack:
                    # 将闭合标签信息添加到栈顶节点
                    stack[-1]["close_tag_name"] = "/" + tag["tag_name"]
                    stack[-1]["close_tag_param"] = None
                    stack[-1]["close_tag_start_idx"] = tag["start_idx"]
                    stack[-1]["close_tag_end_idx"] = tag["end_idx"]
                    # 弹出栈顶
                    stack.pop()

        return root

    # Step 3: 提取标签内容
    def extract_content(node, text):
        if node["tag_name"] == "parameter":
            # 提取 <parameter=key>value</parameter> 的内容
            param_key = node["tag_param"]
            param_value = text[node["start_idx"]:node["end_idx"]]
            return {param_key: param_value}
        elif node["tag_name"] == "list":
            # 提取 <list>...</list> 的内容
            return [extract_content(child, text) for child in node["children"]]
        elif node["tag_name"] == "object":
            # 提取 <object>...</object> 的内容
            return {child["tag_param"]: extract_content(child, text) for child in node["children"]}
        else:
            return text[node["start_idx"]:node["end_idx"]]

    # 执行解析
    tags = extract_tags(input_text)
    tree = build_tree(tags, input_text)
    final_result = {}
    for child in tree["children"]:
        result = parse_tree(child, input_text)
        final_result.update(result)
    return final_result

def parse_tree(node, input_text):
    # 如果当前节点有子节点，递归解析子节点
    if "children" in node and node["children"]:
        # 如果当前节点是 "parameter" 且有 tag_param，构建一个键值对
        if node["tag_name"] == "parameter" and node["tag_param"]:
            # 如果只有一个子节点，直接解析子节点
            if len(node["children"]) == 1:
                return {node["tag_param"]: parse_tree(node["children"][0], input_text)}
            # 如果有多个子节点，解析为列表
            else:
                return {node["tag_param"]: [parse_tree(child, input_text) for child in node["children"]]}
        # 如果当前节点是 "list"，返回子节点的列表
        elif node["tag_name"] == "list":
            result = [parse_tree(child, input_text) for child in node["children"]]
            # import pdb;pdb.set_trace()
            return result
        # 如果当前节点是 "object" 或其他容器类型，返回子节点的合并结果
        elif node["tag_name"] == "object":
            result = {}
            for child in node["children"]:
                child_result = parse_tree(child, input_text)
                # 确保子节点返回的是字典
                if isinstance(child_result, dict):
                    result.update(child_result)
                else:
                    raise RuntimeError("Object return not dict!!!")
                # import pdb;pdb.set_trace()
            return result
        elif node["tag_name"] == "item":
            dict_result = {}
            list_result = []
            is_dict = False
            is_list = False
            for child in node["children"]:
                child_result = parse_tree(child, input_text)
                # 确保子节点返回的是字典
                if isinstance(child_result, dict):
                    dict_result.update(child_result)
                    is_dict = True
                elif isinstance(child_result, list):
                    list_result.extend(child_result)
                    is_list = True
                else:
                    raise RuntimeError("Item with children return not dict or list !!!")
                # import pdb;pdb.set_trace()
            if is_list:
                return list_result
            else:
                return dict_result
    else:
        # 如果当前节点没有子节点，提取实际的值
        end_idx = node["end_idx"]
        close_tag_start_idx = node["close_tag_start_idx"]
        value = input_text[end_idx:close_tag_start_idx]
        if node["tag_name"] == "parameter" and node["tag_param"]:
            return {node["tag_param"]: value}
        else:
            return value
    return {}

def parse_xml_action_v2(content: str, tool_schemas: list) -> list:
    """
    Parse function-style tool calls from the response content.

    Args:
        content (str): 
            The XML-like string containing one or more `<function>` blocks. 
            Each `<function>` block should follow this format:
            ```
            <function=function_name>
                <parameter=parameter_name>parameter_value</parameter>
            </function>
            ```
            Example:
            ```
            <function=click>
                <parameter=point>100 200</parameter>
            </function>
            <function=type>
                <parameter=content>123</parameter>
            </function>
            ```

        tool_schemas (list of dict): 
            A list of tool schema definitions. Each schema should be a dictionary 
            with the following structure:
            ```
            {
                "function": {
                    "name": "function_name",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "parameter_name": {
                                "type": "str",
                                "description": "Description of the parameter."
                            }
                        },
                        "required": ["parameter_name"]
                    }
                }
            }
            ```

    Returns:
        list of dict: 
            A list of parsed tool calls. Each tool call is represented as a dictionary 
            with the following structure:
            ```
            {
                "function": "function_name",
                "parameters": {
                    "parameter_name": "parameter_value"
                }
            }
            ```

    Raises:
        FunctionCallValidationError: 
            If a function name in the content does not match any tool schema.
    """
    tool_calls = []
    tool_schemas = remove_nest_function(tool_schemas)
    # Find all function calls using regex pattern
    fn_matches = re.finditer(FN_REGEX_PATTERN, content, re.DOTALL)

    for fn_match in fn_matches:
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)
        
        # Find matching tool
        matching_schema = None
        for tool_schema in tool_schemas:
            if tool_schema["name"] == fn_name:
                matching_schema = tool_schema
                break

        if not matching_schema:
            raise FunctionCallValidationError(
                f"Function '{fn_name}' not found in available tools: {[tool['name'] for tool in tool_schemas]}"
            )
            
        params = parse_structure_to_tree(fn_body)

        # Create tool call
        tool_calls.append({"function": fn_name, "parameters": params})
        valid = validate_and_fix_data(matching_schema['parameters'], params)
        if not valid:
            raise FunctionCallValidationError(f"Schema '{matching_schema}' valid error")
    return tool_calls

def validate_and_fix_data(schema, data):
    """
    验证数据是否符合给定的 JSON Schema，并尝试修复数据类型问题。

    :param schema: JSON Schema，用于定义数据结构和约束。
    :param data: 要验证的数据。
    :return: 如果数据符合 schema，返回 True；如果修复后符合 schema，返回 True；否则返回 False。
    """
    def fix_data_types(schema, data, param_names):
        """
        根据 JSON Schema 修复数据类型问题。
        :param schema: JSON Schema。
        :param data: 要修复的数据。
        """
        if isinstance(schema, dict) and "type" in schema:
            expected_type = schema["type"]
            for param_name in param_names:
                if expected_type in PARAM_INT_TYPE and isinstance(data, str):
                    try:
                        return int(data)
                    except ValueError as e:
                        raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an integer.") from e
                elif expected_type in PARAM_NUMBER_TYPE and isinstance(data, str):
                    try:
                        return float(data)
                    except ValueError as e:
                        raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an float number.") from e
                elif expected_type in PARAM_BOOL_TYPE and isinstance(data, str):
                    try:
                        if data.lower() == "true":
                            data = True
                        elif data.lower() == "false":
                            data = False
                        else:
                            data = bool(data)
                        return data
                    except ValueError as e:
                        raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an boolean.") from e

        # 如果是对象类型，递归修复
        if isinstance(schema, dict) and "properties" in schema and isinstance(data, dict):
            for key, subschema in schema["properties"].items():
                if key in data:
                    data[key] = fix_data_types(subschema, data[key], [key])

        # 如果是数组类型，递归修复
        if isinstance(schema, dict) and "items" in schema and isinstance(data, list):
            for i in range(len(data)):
                if "properties" in schema:
                    param_names = list(schema["properties"].keys())
                data[i] = fix_data_types(schema["items"], data[i], param_names)

        return data

    try:
        # 尝试直接验证数据
        validate(instance=data, schema=schema)
        return True
    except jsonschema.exceptions.ValidationError as e:
        # 如果验证失败，尝试修复数据类型
        # print(f"Validation Error: {e.message}")
        param_names = list(schema["properties"].keys())
        data = fix_data_types(schema, data, param_names)
        # 再次验证修复后的数据
        try:
            validate(instance=data, schema=schema)
            return True
        except jsonschema.exceptions.ValidationError as e:
            print(f"Validation Error: {e.message}")
            return False
        
def actions_valid_checker(actions, action_space_requirement=None):
    """检查action是否符合我们的动作空间定义"""
    valid_action_type = [
        'click',
        'left_double',
        'right_single',
        'drag',
        'scroll',
        'mouse_down',
        'move_to',
        'mouse_up',
        'type',
        'hotkey',
        'press',
        'release',
        'wait',
        'user_resp',
        'call_user',
        'finished',
        'error_env',
        'open_computer',
    ]
    if (
        any(action.action_type.value in ['call_user', 'user_resp', 'finished'] for action in actions)
        and len(actions) != 1
    ):
        return 'call_user, user_resp, finished出现时，当前step只能有这一个action'

    for action_id, action in enumerate(actions):
        if action.action_type.value not in valid_action_type:
            return f'[{action_id}]action类型只能是{valid_action_type}中的一种'

        if action_space_requirement is not None:
            if (
                'finished_has_content' in action_space_requirement
                and action_space_requirement['finished_has_content']
                and action.action_type.value == 'finished'
            ) and 'content' not in action.custom_data:
                return f'[{action_id}]finished操作需要有content参数(因为system prompt要求了)'
            if (
                'finished_has_content' in action_space_requirement
                and not action_space_requirement['finished_has_content']
                and action.action_type.value == 'finished'
            ) and action.custom_data != {}:
                return f'[{action_id}]finished操作不能有参数(因为system prompt要求了)'

            if (
                'has_call_user' in action_space_requirement
                and not action_space_requirement['has_call_user']
                and action.action_type.value == 'call_user'
            ):
                return f'[{action_id}]call_user操作不能出现(因为system prompt要求了)'

        if action.action_type.value == 'type':
            if set(action.custom_data.keys()) != {'content'}:
                return f'[{action_id}]type操作只能有一个content参数'
            if action.custom_data['content'] == '':
                return f'[{action_id}]type操作的content不能为空'

        if action.action_type.value in ['hotkey', 'press', 'release']:
            if set(action.custom_data.keys()) != {'key'}:
                return f'[{action_id}]hotkey/press/release操作只能有一个key参数'
            if action.custom_data['key'] == '':
                return f'[{action_id}]hotkey/press/release操作的key不能为空'
            all_key = action.custom_data['key'].split(' ')
            if action.action_type.value in ['press', 'release'] and len(all_key) != 1:
                return f'[{action_id}]press/release操作的key只能有一个'
            for key in all_key:
                # key必须是全小写
                if key.lower() != key:
                    return f'[{action_id}]hotkey操作的key必须是全小写'
                # if key in ["up", "down", "left", "right"]:
                #     return "方向键的名字是 arrowup, arrowdown, arrowleft, arrowright"

        if action.action_type.value in ['click', 'left_double', 'right_single']:
            if set(action.custom_data.keys()) != {'start_box'}:
                return f'[{action_id}]click, left_double, right_single操作只能有一个start_box参数'
            for coor in action.custom_data['start_box']:
                if coor > 1 or coor < 0:
                    return f'[{action_id}]click, left_double, right_single操作的box的坐标必须在0-1之间(你写的数字得是0-999之间的)'

        if action.action_type.value == 'drag':
            if set(action.custom_data.keys()) != {'start_box', 'end_box'}:
                return f'[{action_id}]drag操作必须是两个参数: start_box和end_box。（没有middle box，就是起点和终点）'
            for coor in action.custom_data['start_box']:
                if coor > 1 or coor < 0:
                    return f'[{action_id}]drag操作的start_box的坐标必须在0-1之间(你写的数字得是0-999之间的)'
            for coor in action.custom_data['end_box']:
                if coor > 1 or coor < 0:
                    return f'[{action_id}]drag操作的end_box的坐标必须在0-1之间(你写的数字得是0-999之间的)'

        if action.action_type.value == 'scroll':
            if set(action.custom_data.keys()) != {'start_box', 'direction'}:
                return f'[{action_id}]scroll操作必须是两个参数: start_box和direction'
            for coor in action.custom_data['start_box']:
                if coor > 1 or coor < 0:
                    return f'[{action_id}]scroll操作的start_box的坐标必须在0-1之间'
            if action.custom_data['direction'] not in ['up', 'down', 'left', 'right']:
                return f'[{action_id}]scroll操作的方向只能是up, down, left, right。（注意你是不是多打了个引号）'

        if action.action_type.value in ['wait', 'call_user', 'user_resp'] and set(action.custom_data.keys()) != set():
            return f'[{action_id}]wait/call_user/user_resp操作不能有参数'

        if action.action_type.value == 'finished' and action.custom_data != {}:
            if set(action.custom_data.keys()) != {'content'}:
                return f'[{action_id}]finished操作只能有一个content参数'
            if action.custom_data['content'] == '':
                return f'[{action_id}]finished操作的content不能为空'

    return True

def ast_parse(action_str):
    try:
        # 解析字符串为 AST 节点
        node = ast.parse(action_str, mode='eval')

        # 确保节点是一个表达式
        if not isinstance(node, ast.Expression):
            raise ValueError('Not an expression')

        # 获取表达式的主体
        call = node.body

        # 确保主体是一个函数调用
        if not isinstance(call, ast.Call):
            raise ValueError('Not a function call')

        # 获取函数名
        if isinstance(call.func, ast.Name):
            func_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            func_name = call.func.attr
        else:
            func_name = None

        # 获取关键字参数
        kwargs = {}
        for kw in call.keywords:
            key = kw.arg
            # 处理不同类型的值，这里假设都是常量
            if isinstance(kw.value, ast.Constant):
                value = kw.value.value
            elif isinstance(kw.value, ast.Str):  # 兼容旧版本 Python
                value = kw.value.s
            else:
                value = None
            kwargs[key] = value

        return {'function': func_name, 'args': kwargs}

    except Exception as e:  # 问题向外层传送，看看是不是外层的问题，比如少传入了几行文字
        # import pdb; pdb.set_trace()
        print(f"Failed to parse action '{action_str}': {e}")
        return None
    
def parse_action_to_structure_output_v2(raw_response: str):
    """
    解析raw_response，返回解析结果，以及action是否合法
    """
    if 'Action: ' not in raw_response:
        return None, False
    thought = raw_response.split('Action: ')[0].strip()
    if thought.startswith('Thought:'):
        thought = thought[len('Thought:') :].strip()

    action_str = raw_response.split('Action: ')[1].strip()
    action_list = action_str.split('\n\n')

    parsed_action_list = []
    parsed_action_list_remain = []
    for action in action_list:
        new_action = ast_parse(action)
        if new_action is None:
            return None, False
        # action name是否合法。遍历GUIActionType
        if new_action['function'] not in [action_type.value for action_type in GUIActionType]:
            return None, False
        parsed_action = GUIAction(action_type=GUIActionType(new_action['function']), custom_data={})
        parsed_action_remain = GUIAction(action_type=GUIActionType(new_action['function']), custom_data={})
        for param_name, param_value in new_action['args'].items():
            remain_param_value = deepcopy(param_value)
            if param_name in ['start_box', 'end_box', 'start_point', 'end_point', 'point']:
                if param_value.startswith('<bbox>') and param_value.endswith('</bbox>'):
                    param_value = param_value[len('<bbox>') : -len('</bbox>')]
                    param_value = param_value.split(' ')
                elif param_value.startswith('<point>') and param_value.endswith('</point>'):
                    param_value = param_value[len('<point>') : -len('</point>')]
                    param_value = param_value.split(' ')
                else:
                    return None, False

                if len(param_value) != 4 and len(param_value) != 2:
                    return None, False
                param_value = [eval(x) / 1000 for x in param_value]
                if len(param_value) == 2:  # 把数字重复一遍，是历史遗留问题
                    param_value = param_value + param_value

            if param_name == 'start_point':
                param_name = 'start_box'
            elif param_name == 'end_point':
                param_name = 'end_box'
            elif param_name == 'point':
                param_name = 'start_box'
            parsed_action.custom_data[param_name] = param_value
            parsed_action_remain.custom_data[param_name] = remain_param_value
        parsed_action_list.append(parsed_action)
        parsed_action_list_remain.append(parsed_action_remain)

    # 确认response可以正常解析以后，接下来检查字段的合法性
    is_valid = actions_valid_checker(parsed_action_list)
    if isinstance(is_valid, str):
        return None, False

    return {'thought': thought, 'actions': parsed_action_list, 'actions_remain': parsed_action_list_remain}, True

def format_transfer(
    text,
    ):
    old_parsed_result, parse_success = parse_action_to_structure_output_v2(text)
    if not parse_success:
        return None
    thought = old_parsed_result["thought"]
    actions = old_parsed_result["actions_remain"]
    think_content = f"{thought}\n"
    begin_tool_call_token = "<seed:tool_call>"
    end_tool_call_token = "</seed:tool_call>"
    function_content = ""
    for action in actions:
        action_type = action.action_type.value
        action_inputs = action.custom_data
        if "start_box" in action_inputs and "end_box" in action_inputs:
            action_inputs["start_point"] = action_inputs["start_box"]
            del action_inputs["start_box"]
            action_inputs["end_point"] = action_inputs["end_box"]
            del action_inputs["end_box"]
        
        function_content += f"\n<function={action_type}>"
        for key, value in action_inputs.items():
            function_str = f"\n<parameter={key}>{value}</parameter>"
            function_content += function_str
        function_content += f"\n</function>\n"
    final_content = f"{think_content}{begin_tool_call_token}{function_content}{end_tool_call_token}"
    return final_content

    
if __name__ == '__main__':
    
    input_text = """<gui_think>好的，现在文档内容已经全部选中了。我注意到工具栏上有个背景颜色的按钮，旁边有个小箭头，点击它就能打开颜色选择面板。我需要在里面选择"无填充"选项，这样就能一次性清除所有文字的黄色高亮了。</gui_think>
<seed:tool_call>
<function=click>
<parameter=point><object><parameter=x>539</parameter><parameter=y>130</parameter></object></parameter>
</function>
</seed:tool_call>"""
    tool_schemas = [
        {
            "type": "function",
            "name": "click",
            "parameters": {
                "type": "object",
                "properties": {
                    "point": {
                        "type": "object",
                        "properties": {
                            "x": {
                                "type": "integer",
                                "description": "Click coordinates. The format is: <point>x y</point>"
                            },
                            "y": {
                                "type": "integer",
                                "description": "Click coordinates. The format is: <point>x y</point>"
                            },
                            "z": {
                                "type": "integer",
                                "description": "Click coordinates. The format is: <point>x y</point>"
                            }
                        },
                        "required": [
                            "x",
                            "y"
                        ]
                    }
                },
                
            },
            "description": "Mouse left single click action."
        }
    ]
    parsed_toolcalls = parse_xml_action_v2(input_text, tool_schemas,)
    print(parsed_toolcalls)
    input()
    image_height = 1080
    image_width = 1920
    def convert_point_to_coordinates(text, is_answer=False):
        # 匹配 <bbox> 后面的四个数字
        pattern = r"<point>(\d+)\s+(\d+)</point>"
        
        def replace_match(match):
            x1, y1= map(int, match.groups())
            x = (x1 + x1) // 2  # 使用截断取整
            y = (y1 + y1) // 2  # 使用截断取整
            if is_answer:
                return f"({x},{y})"  # 只返回 (x, y) 格式
            return f"({x},{y})"  # 返回带标签的格式
        
        # 去掉 [EOS] 并替换 <bbox> 坐标
        text = re.sub(r"\[EOS\]", "", text)
        return re.sub(pattern, replace_match, text).strip()
    content = """<gui_think>我看到左侧的航空公司筛选栏里有个"Other"选项，这应该就是我要找的。毕竟Aer Lingus作为一家欧洲航空公司，很可能被归类在"其他"类别下。让我点击这个复选框，这样就能过滤出更多航班选项，找到符合时间要求的爱尔兰航空航班。</gui_think>\n<seed:tool_call><function=click><parameter=point><function name="click">
<parameter name="point"><point>34 325</point></parameter>
</function></parameter></function><function=click><parameter=point><point>123 638</point></parameter></function><function=click><parameter=point><point>223 562</point></parameter></function></seed:tool_call>"""
    print(convert_point_to_coordinates(content))
    tool_schemas = [
    {
        "type": "function",
        "function": {
            "name": "click",
            "parameters": {
                "type": "object",
                "properties": {
                    "point": {
                        "type": "string",
                        "description": "Click coordinates. The format is: <point>x y</point>"
                    }
                },
                "required": [
                    "point"
                ]
            },
            "description": "Mouse left single click action."
        }
    }]
    parsed_xml_actions = parse_xml_action(content, tool_schemas)
    print(parsed_xml_actions)
    # print(a[0]["parameters"]["content"])
    actions = []
    for xml_action in parsed_xml_actions:
        actions.append(
            {
                "action_type": xml_action["function"],
                "action_inputs": xml_action["parameters"]
            }
        )
    result = parsing_response_to_pyautogui_code(actions, image_height, image_width)
    print(result)
    
    old_format = "Thought: 点击按钮\nAction: drag(start_point='<point>100 100</point>',end_point='<point>200 300</point>')"
    new_format = format_transfer(old_format)
    print("old format: ", old_format)
    print("new format: ", new_format)
    
    a = parse_xml_action(new_format, tool_schemas)
    print(a)