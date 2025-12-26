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
FN_REGEX_PATTERN_65 = r'<function name="([^>]+)">\n?(.*?)</function>'
FN_PARAM_REGEX_PATTERN = r'<parameter=([^>]+)>(.*?)</parameter>'
FN_PARAM_REGEX_PATTERN_06 = r'<parameter=([^, ]+) string="(true|false)">(.*?)</parameter>'
FN_PARAM_REGEX_PATTERN_65 = r'<parameter name="([^, ]+)" string="(true|false)">(.*?)</parameter>'


FN_REGEX_PATTERN_V3 = r'<function_never_used_51bce0c785ca2f68081bfa7d91973934=([^>]+)>\n?(.*?)</function_never_used_51bce0c785ca2f68081bfa7d91973934>'
FN_PARAM_REGEX_PATTERN_V3 = r'<parameter_never_used_51bce0c785ca2f68081bfa7d91973934=([^>]+)>(.*?)</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>'


PARAM_INT_TYPE = [
    "integer",
    "int"
]

PARAM_NUMBER_TYPE = ["float", "double", "real", "number"]

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

PARAM_OBJECT_TYPE = ["dict", "obj", "object"]

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
            # if content.endswith("\n") or content.endswith("\\n"):
            #     stripped_content = stripped_content.rstrip("\\n").rstrip("\n")
            if content:
                if input_swap:
                    pyautogui_code += f"\nimport pyperclip"
                    # if "\"" in stripped_content:
                    #     stripped_content = stripped_content.replace("\"", "\\\"")
                    pyautogui_code += f'\npyperclip.copy({repr(stripped_content)})'
                    pyautogui_code += f"\npyautogui.hotkey('ctrl', 'v')"
                    pyautogui_code += f"\ntime.sleep(0.5)\n"
                    if content.endswith("\n") or content.endswith("\\n"):
                        pyautogui_code += f"\npyautogui.press('enter')"
                else:
                    pyautogui_code += f'\npyautogui.write({repr(stripped_content)}, interval=0.1)'
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

def _extract_and_validate_params_06(param_matches: Iterable[re.Match]) -> dict:
    def to_json(value) -> str:
        try:
            return json.dumps(value, ensure_ascii=False)
        except:
            return json.dumps(value, ensure_ascii=True)
    def _decode_value(key: str, value: str, string: str):
        if string == "true":
            value = to_json(value)
        else:
            if value.lower() == "false":
                value = "false"
            elif value.lower() == "true":
                value = "true"
            elif value.lower() == "none" or value.lower() == "null":
                value = "null"
        return f"{to_json(key)}:{value}"

    tool_args = {}
    for param_match in param_matches:
        parameter_name = param_match.group(1)  # 第一个捕获组：parameter 名称
        is_str = param_match.group(2)   # 第二个捕获组：string 的值（true/false 或 None）
        content = param_match.group(3)
        tool_args[parameter_name] = (content, is_str)
    tool_args_json = "{" + ", ".join([_decode_value(k, v, string=is_str) for k, (v, is_str) in tool_args.items()]) + "}"
    return json.loads(tool_args_json)
    
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

def parse_structure_to_tree(input_text, parameter_token):
    # Step 1: 提取所有标签信息
    def extract_tags(text):
        # 定义正则表达式模式
        tag_pattern = r"<(/?)(\w+)(?:=([^>]+))?>"
        # 定义需要提取的特定标签
        allowed_tags = {parameter_token, "item", "object", "list"}
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
        if node["tag_name"] == parameter_token:
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
        result = parse_tree(child, input_text, parameter_token)
        final_result.update(result)
    return final_result

def parse_tree(node, input_text, parameter_token):
    # 如果当前节点有子节点，递归解析子节点
    if "children" in node and node["children"]:
        # 如果当前节点是 "parameter" 且有 tag_param，构建一个键值对
        if node["tag_name"] == parameter_token and node["tag_param"]:
            # 如果只有一个子节点，直接解析子节点
            if len(node["children"]) == 1:
                return {node["tag_param"]: parse_tree(node["children"][0], input_text, parameter_token)}
            # 如果有多个子节点，解析为列表
            else:
                return {node["tag_param"]: [parse_tree(child, input_text, parameter_token) for child in node["children"]]}
        # 如果当前节点是 "list"，返回子节点的列表
        elif node["tag_name"] == "list":
            if input_text == "\n":
                return []
            result = [parse_tree(child, input_text, parameter_token) for child in node["children"]]
            # import pdb;pdb.set_trace()
            return result
        # 如果当前节点是 "object" 或其他容器类型，返回子节点的合并结果
        elif node["tag_name"] == "object":
            result = {}
            for child in node["children"]:
                child_result = parse_tree(child, input_text, parameter_token)
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
                child_result = parse_tree(child, input_text, parameter_token)
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
        if node["tag_name"] == parameter_token and node["tag_param"]:
            return {node["tag_param"]: value}
        else:
            if node["tag_name"]=='list':
                if value.strip() == "":
                    return []
            return value
    return {}

def extract_text_never_used_content(input_string):
    """
    从输入字符串中提取被 <text_never_used_51bce0c785ca2f68081bfa7d91973934> 标签包裹的内容。
    
    :param input_string: 输入的字符串
    :return: 一个字典，key 是原始的标签和内容，value 是实际的内容
    """
    # 定义正则表达式，匹配 <text_never_used_51bce0c785ca2f68081bfa7d91973934>...</text_never_used_51bce0c785ca2f68081bfa7d91973934>
    pattern = r"(<text_never_used_51bce0c785ca2f68081bfa7d91973934>.*?</text_never_used_51bce0c785ca2f68081bfa7d91973934>)"
    
    # 查找所有匹配的内容
    matches = re.findall(pattern, input_string, re.DOTALL)
    
    # 创建结果字典
    result = {}
    result_map = {}
    result_map_reverse = {}
    
    map_count = 0
    for match in matches:
        # 提取实际内容（去掉外层的标签）
        content_pattern = r"<text_never_used_51bce0c785ca2f68081bfa7d91973934>(.*?)</text_never_used_51bce0c785ca2f68081bfa7d91973934>"
        content = re.search(content_pattern, match, re.DOTALL).group(1)
        
        # 将原始标签和内容作为 key，实际内容作为 value
        result[match] = content.strip()
        placeholder = f"text_never_used_51bce0c785ca2f68081bfa7d91973934_{map_count}"
        result_map[placeholder] = match.replace("<text_never_used_51bce0c785ca2f68081bfa7d91973934>", "").replace("</text_never_used_51bce0c785ca2f68081bfa7d91973934>", "")
        result_map_reverse[match] = placeholder
        map_count += 1
    
    return result_map, result_map_reverse

def extract_type_and_value(input_string):
    # 定义正则表达式
    pattern = r"^<type=(\w+)>(.*)</type>$"
    match = re.match(pattern, input_string)
    if match:
        param_type = match.group(1)  # 捕获类型值
        param_value = match.group(2)  # 捕获参数值
        return param_type, param_value  # 返回类型和值
    return None, None  # 如果不匹配，返回 None, None

def convert_param_value(param_type, param_value):
    """
    根据 param_type 转换 param_value
    """
    try:
        if param_type == "integer":
            return int(param_value)  # 转换为整数
        elif param_type == "float":
            return float(param_value)  # 转换为浮点数
        elif param_type == "boolean":
            # 转换为布尔值，支持 true/false（大小写不敏感）
            return param_value.lower() == "true"
        elif param_type == "string":
            return param_value  # 保持为字符串
        elif param_type == "array":
            # 尝试将参数值解析为 Python 列表
            return json.loads(param_value)
        elif param_type == "object":
            # 尝试将参数值解析为 Python 字典
            return json.loads(param_value)
        else:
            raise ValueError(f"未知的参数类型: {param_type}")
    except Exception as e:
        print(f"无法将值 '{param_value}' 转换为类型 '{param_type}': {e}")
        return param_value
    
def set_leaf_values(params, result_map):
    """
    遍历嵌套字典，将所有叶子节点的值设置为 1。
    
    :param params: 输入的嵌套字典
    :return: 修改后的字典
    """
    if isinstance(params, dict):
        # 如果当前节点是字典，递归处理每个键值对
        for key in params:
            params[key] = set_leaf_values(params[key], result_map)
    elif isinstance(params, list):
        # 如果当前节点是列表，递归处理每个元素
        params = [set_leaf_values(item, result_map) for item in params]
    elif isinstance(params, str):
        # 如果是叶子节点（非字典或列表），将值设置为 1
        if params.strip().startswith("<type=") and params.strip().endswith("</type>"):
            param_type, param_value = extract_type_and_value(params)
            params = convert_param_value(param_type, param_value)
        for placeholder in result_map:
            match = result_map[placeholder]
            params = params.replace(placeholder, match)
    else:
        if params.strip().startswith("<type=") and params.strip().endswith("</type>"):
            param_type, param_value = extract_type_and_value(params)
            params = convert_param_value(param_type, param_value)
        params = params
    return params

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
    
    # 抽取text never used
    result_map, result_map_reverse = extract_text_never_used_content(content)
    for match in result_map_reverse:
        placeholder = result_map_reverse[match]
        content = content.replace(match, placeholder)
        
    # Find all function calls using regex pattern
    fn_matches = re.finditer(FN_REGEX_PATTERN, content, re.DOTALL)
    found = False
    parameter_token = "parameter"
    for fn_match in fn_matches:
        found = True
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
        
        params = parse_structure_to_tree(fn_body, parameter_token)
        params = set_leaf_values(params, result_map)
        
        # Create tool call
        tool_calls.append({"function": fn_name, "parameters": params})
        valid, error_info = validate_and_fix_data(matching_schema['parameters'], params)
        if not valid:
            raise FunctionCallValidationError(f"Valid error of schema:\n{matching_schema}\nError info: {error_info}")
    if "<seed:tool>" in content and not found:
        raise RuntimeError(f"Detected tool call token but parse function schema failed with regex: {FN_REGEX_PATTERN}")
    return tool_calls

def parse_xml_action_v3(content: str, tool_schemas: list) -> list:
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
    
    # 抽取text never used
    result_map, result_map_reverse = extract_text_never_used_content(content)
    for match in result_map_reverse:
        placeholder = result_map_reverse[match]
        content = content.replace(match, placeholder)
    # Find all function calls using regex pattern
    fn_matches = re.finditer(FN_REGEX_PATTERN_V3, content, re.DOTALL)
    parameter_token = "parameter_never_used_51bce0c785ca2f68081bfa7d91973934"
    found = False
    for fn_match in fn_matches:
        found = True
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
        
        params = parse_structure_to_tree(fn_body, parameter_token)
        params = set_leaf_values(params, result_map)
        
        # Create tool call
        tool_calls.append({"function": fn_name, "parameters": params})
        valid, error_info = validate_and_fix_data(matching_schema['parameters'], params)
        if not valid:
            raise FunctionCallValidationError(f"Valid error of schema:\n{matching_schema}\nError info: {error_info}")
    if "<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>" in content and not found:
        raise RuntimeError(f"Detected tool call token but parse function schema failed with regex: {FN_REGEX_PATTERN_V3}")
    return tool_calls

def parse_xml_action_04(content: str, tool_schemas: list) -> list:
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
    
    if "<list>\n</list>" in content:
        content = content.replace("<list>\n</list>", "<list></list>")
    # 抽取text never used
    result_map, result_map_reverse = extract_text_never_used_content(content)
    for match in result_map_reverse:
        placeholder = result_map_reverse[match]
        content = content.replace(match, placeholder)
    # Find all function calls using regex pattern
    fn_matches = re.finditer(FN_REGEX_PATTERN_V3, content, re.DOTALL)
    parameter_token = "parameter_never_used_51bce0c785ca2f68081bfa7d91973934"
    found = False
    for fn_match in fn_matches:
        found = True
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
        
        params = parse_structure_to_tree(fn_body, parameter_token)
        params = set_leaf_values(params, result_map)
        
        # Create tool call
        tool_calls.append({"function": fn_name, "parameters": params})
        valid, error_info = validate_and_fix_data(matching_schema['parameters'], params)
        if not valid:
            raise FunctionCallValidationError(f"Valid error of schema:\n{matching_schema}\nError info: {error_info}")
    if "<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>" in content and not found:
        raise RuntimeError(f"Detected tool call token but parse function schema failed with regex: {FN_REGEX_PATTERN_V3}")
    return tool_calls

def parse_xml_action_v5(content: str) -> list:
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
    
    # 抽取text never used
    result_map, result_map_reverse = extract_text_never_used_content(content)
    for match in result_map_reverse:
        placeholder = result_map_reverse[match]
        content = content.replace(match, placeholder)
        
    # Find all function calls using regex pattern
    fn_matches = re.finditer(FN_REGEX_PATTERN, content, re.DOTALL)
    found = False
    parameter_token = "parameter"
    for fn_match in fn_matches:
        found = True
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)
        
        params = parse_structure_to_tree(fn_body, parameter_token)
        params = set_leaf_values(params, result_map)
        
        # Create tool call
        tool_calls.append({"function": fn_name, "parameters": params})
        
    if "<seed:tool>" in content and not found:
        raise RuntimeError(f"Detected tool call token but parse function schema failed with regex: {FN_REGEX_PATTERN}")
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
                        raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an integer, but found value {data}.") from e
                elif expected_type in PARAM_NUMBER_TYPE and isinstance(data, str):
                    try:
                        return float(data)
                    except ValueError as e:
                        raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an float number, but found value {data}.") from e
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
                        raise FunctionCallValidationError(f"Parameter '{param_name}' is expected to be an boolean, but found value {data}.") from e

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
                if not data[i]:
                    data[i] = []
                data[i] = fix_data_types(schema["items"], data[i], param_names)

        if not data:
            # 如果是空的
            if isinstance(schema, dict) and "items" in schema:
                data = []
            if isinstance(schema, dict) and "properties" in schema:
                data = {}
            
        return data

    try:
        # 尝试直接验证数据
        validate(instance=data, schema=schema)
        return True, ""
    except jsonschema.exceptions.ValidationError as e:
        # 如果验证失败，尝试修复数据类型
        # print(f"Validation Error: {e.message}")
        param_names = list(schema["properties"].keys())
        data = fix_data_types(schema, data, param_names)
        # 再次验证修复后的数据
        try:
            validate(instance=data, schema=schema)
            return True, ""
        except jsonschema.exceptions.ValidationError as e:
            print(f"Validation Error: {e.message}")
            return False, e.message
        
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

def format_transfer_v2(
    text,
    think_token,
    check_valid
    ):
    if "Action: hotkey(hotkey='" in text:
        text = text.replace("Action: hotkey(hotkey='", "Action: hotkey(key='")
    old_parsed_result, parse_success = parse_action_to_structure_output_v2(text, check_valid)
    if not parse_success:
        return None
    thought = old_parsed_result["thought"]
    actions = old_parsed_result["actions_remain"]
    think_content = f"<{think_token}>{thought}</{think_token}>\n"
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
        elif "start_box" in action_inputs:
            action_inputs["point"] = action_inputs["start_box"]
            del action_inputs["start_box"]
        
        function_content += f"<function={action_type}>"
        for key, value in action_inputs.items():
            function_str = f"<parameter={key}>{value}</parameter>"
            function_content += function_str
        function_content += f"</function>"
    final_content = f"{think_content}{begin_tool_call_token}{function_content}{end_tool_call_token}"
    return final_content

def convert_param_value_to_xml(param_value, parameter_name_token, parameter_close_token, indent_level=0):
    """
    将参数值转换为XML格式，支持复杂的嵌套结构

    支持的格式：
    - <object> </object>: JSON对象
    - <list> </list>: 列表
    - <text_never_used_51bce0c785ca2f68081bfa7d91973934> </text_never_used_51bce0c785ca2f68081bfa7d91973934>: XML内容
    - 简单文本: 直接内容（支持多行）

    Args:
        param_value: 参数值，可以是任何类型
        indent_level: 缩进级别，用于格式化输出

    Returns:
        str: 转换后的XML字符串
    """
    indent = ""  # 4个空格作为一级缩进

    if isinstance(param_value, dict):
        # 检查是否为特殊的XML内容标记
        if len(param_value) == 1 and '_xml_content_' in param_value:
            # 特殊处理XML内容
            xml_content = param_value['_xml_content_']
            return f"{indent}<text_never_used_51bce0c785ca2f68081bfa7d91973934>{indent}{xml_content}{indent}</text_never_used_51bce0c785ca2f68081bfa7d91973934>"

        # 对于普通字典类型，转换为嵌套的XML格式
        xml_parts = [f"\n{indent}<object>"]
        for key, value in param_value.items():
            converted_value = convert_param_value_to_xml(value, parameter_name_token, parameter_close_token, indent_level + 2)
            if _is_complex_value(converted_value):
                # 复杂值（包含换行或嵌套结构）
                xml_parts.append(f"{indent}{parameter_name_token}{key}>{converted_value}{indent}{parameter_close_token}")
            else:
                # 简单值
                xml_parts.append(f"{indent}{parameter_name_token}{key}>{converted_value}{parameter_close_token}")
        xml_parts.append(f"{indent}</object>")
        return "".join(xml_parts)

    elif isinstance(param_value, list):
        # 对于列表类型，转换为包含多个元素的XML格式
        if len(param_value) == 0:
            return f"{indent}<list>{indent}</list>"

        xml_parts = [f"{indent}<list>"]
        for item in param_value:
            converted_item = convert_param_value_to_xml(item, parameter_name_token, parameter_close_token, indent_level + 2)
            if _is_complex_value(converted_item):
                # 复杂项（包含换行或嵌套结构）
                xml_parts.append(f"{indent}<item>{converted_item}{indent}</item>")
            else:
                # 简单项
                xml_parts.append(f"{indent}<item>{converted_item}</item>")
        xml_parts.append(f"{indent}</list>")
        return "".join(xml_parts)

    else:
        # 对于简单类型（字符串、数字、布尔值等）
        str_value = str(param_value)

        # 检查是否包含XML标签，如果是则需要特殊处理
        if _contains_xml_tags(str_value):
            return f"{indent}<text_never_used_51bce0c785ca2f68081bfa7d91973934>{_indent_text(str_value, indent_level + 1)}{indent}</text_never_used_51bce0c785ca2f68081bfa7d91973934>"

        # 检查是否为多行文本
        if '\n' in str_value:
            return f"\n{_indent_text(str_value, indent_level)}"

        return str_value


def _is_complex_value(value_str):
    """检查值是否为复杂值（包含换行或XML标签）"""
    return '\n' in value_str or value_str.strip().startswith('<')


def _contains_xml_tags(text):
    """检查文本是否包含XML标签或可能干扰XML解析的字符"""
    import re
    # 检查是否包含 < 或 > 字符，这些字符可能干扰XML解析
    # 即使是比较运算符 (<, >, <=, >=) 也需要包装，因为会干扰参数提取
    return bool(re.search(r'[<>]', text))


def _indent_text(text, indent_level):
    """为多行文本添加缩进"""
    indent = "    " * indent_level
    lines = text.split('\n')
    return ''.join(f"{indent}{line}" if line.strip() else "" for line in lines)


def parse_xml_param_value(xml_content):
    """
    从XML格式解析参数值回JSON格式

    Args:
        xml_content: XML格式的参数内容

    Returns:
        解析后的Python值（dict/list/str/etc）
    """
    import re

    # 检查是否以换行符开头（我们的多行文本格式）
    if xml_content.startswith('\n'):
        return _normalize_indented_text(xml_content[1:])

    # 否则正常处理
    xml_content = xml_content.strip()

    # 处理 <object> 标签
    if xml_content.startswith('<object>') and xml_content.endswith('</object>'):
        return _parse_xml_object(xml_content)

    # 处理 <list> 标签
    elif xml_content.startswith('<list>') and xml_content.endswith('</list>'):
        return _parse_xml_list(xml_content)

    # 处理 <text_never_used_51bce0c785ca2f68081bfa7d91973934> 标签
    elif '<text_never_used_51bce0c785ca2f68081bfa7d91973934>' in xml_content:
        text_pattern = r'<text_never_used_51bce0c785ca2f68081bfa7d91973934>(.*?)</text_never_used_51bce0c785ca2f68081bfa7d91973934>'
        match = re.search(text_pattern, xml_content, re.DOTALL)
        if match:
            content = match.group(1)
            # 移除前后空行和统一缩进
            return _normalize_indented_text(content)

    # 处理多行文本（检查是否有缩进）
    if '\n' in xml_content:
        return _normalize_indented_text(xml_content)

    # 处理简单文本
    return xml_content


def _normalize_indented_text(text):
    """标准化缩进的文本，移除公共缩进"""
    lines = text.split('\n')

    # 移除前后空行
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return ""

    # 找到最小缩进（忽略空行）
    non_empty_lines = [line for line in lines if line.strip()]
    if not non_empty_lines:
        return ""

    min_indent = min(len(line) - len(line.lstrip()) for line in non_empty_lines)

    # 移除公共缩进
    normalized_lines = []
    for line in lines:
        if line.strip():  # 非空行
            normalized_lines.append(line[min_indent:] if len(line) >= min_indent else line)
        else:  # 空行
            normalized_lines.append("")

    return ''.join(normalized_lines)


def _parse_xml_object(xml_content):
    """解析XML object标签"""
    import re

    # 提取object内容
    content = xml_content[8:-9]  # 移除<object>和</object>
    result = {}

    # 查找所有parameter标签
    param_pattern = r'<parameter=([^>]+)>(.*?)</parameter>'
    matches = re.findall(param_pattern, content, re.DOTALL)

    for param_name, param_content in matches:
        result[param_name] = parse_xml_param_value(param_content.strip())

    return result


def _parse_xml_list(xml_content):
    """解析XML list标签"""
    import re

    # 提取list内容
    content = xml_content[6:-7]  # 移除<list>和</list>
    result = []

    # 查找所有item标签
    item_pattern = r'<item>(.*?)</item>'
    matches = re.findall(item_pattern, content, re.DOTALL)

    for item_content in matches:
        result.append(parse_xml_param_value(item_content.strip()))

    return result

def parse_xml_action_v02(
    content,
):
    FN_REGEX_PATTERN_TMP = r"<function_never_used_51bce0c785ca2f68081bfa7d91973934=([^>]+)>(.*?)</function_never_used_51bce0c785ca2f68081bfa7d91973934>"
    function_matches = re.finditer(FN_REGEX_PATTERN_TMP, content, re.DOTALL)
    funciton_calls = []
    for fn_match in function_matches:
        FN_PARAM_REGEX_PATTERN_TMP = r"<parameter_never_used_51bce0c785ca2f68081bfa7d91973934=([^>]+)>(.*?)</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>"
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)

        arguments = {}

        for arg_match in re.finditer(FN_PARAM_REGEX_PATTERN_TMP, fn_body, re.DOTALL):
            arg_name = arg_match.group(1)
            arg_value = arg_match.group(2)
            try:
                arg_value = json.loads(arg_value)
            except Exception:
                pass
            arguments[arg_name] = arg_value
        funciton_calls.append({"function": fn_name, "parameters": arguments})
    return funciton_calls

def parse_xml_action_02sptoken(
    content,
):
    FN_REGEX_PATTERN_TMP = r"<function_never_used_51bce0c785ca2f68081bfa7d91973934=([^>]+)>(.*?)</function_never_used_51bce0c785ca2f68081bfa7d91973934>"
    function_matches = re.finditer(FN_REGEX_PATTERN_TMP, content, re.DOTALL)
    funciton_calls = []
    for fn_match in function_matches:
        FN_PARAM_REGEX_PATTERN_TMP = r"<parameter_never_used_51bce0c785ca2f68081bfa7d91973934=([^>]+)>(.*?)</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>"
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)

        arguments = {}

        for arg_match in re.finditer(FN_PARAM_REGEX_PATTERN_TMP, fn_body, re.DOTALL):
            arg_name = arg_match.group(1)
            arg_value = arg_match.group(2)
            try:
                arg_value = json.loads(arg_value)
            except Exception:
                pass
            arguments[arg_name] = arg_value
        funciton_calls.append({"function": fn_name, "parameters": arguments})
    return funciton_calls

def parse_xml_action_02(
    content,
):
    FN_REGEX_PATTERN_TMP = r"<function=([^>]+)>(.*?)</function>"
    function_matches = re.finditer(FN_REGEX_PATTERN_TMP, content, re.DOTALL)
    funciton_calls = []
    for fn_match in function_matches:
        FN_PARAM_REGEX_PATTERN_TMP = r"<parameter=([^>]+)>(.*?)</parameter>"
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)

        arguments = {}

        for arg_match in re.finditer(FN_PARAM_REGEX_PATTERN_TMP, fn_body, re.DOTALL):
            arg_name = arg_match.group(1)
            arg_value = arg_match.group(2)
            try:
                arg_value = json.loads(arg_value)
            except Exception:
                pass
            arguments[arg_name] = arg_value
        funciton_calls.append({"function": fn_name, "parameters": arguments})
    return funciton_calls

def is_single_layer(d):
    """
    判断一个字典是否是单层。
    如果字典的任意值是字典类型，则认为它是多层。
    """
    if not isinstance(d, dict):
        raise ValueError("Input must be a dictionary.")
    
    for value in d.values():
        if isinstance(value, dict):
            return False  # 存在嵌套的子字典，返回 False
        elif isinstance(value, list):
            return False  # 存在嵌套的子列表，返回 False

    return True  # 没有嵌套的子字典，返回 True

def parse_xml_action_06(content: str) -> list:
    """
    """
    tool_calls = []
    # Find all function calls using regex pattern
    fn_matches = re.finditer(FN_REGEX_PATTERN, content, re.DOTALL)
    for fn_match in fn_matches:
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)
        # Parse parameters
        param_matches = re.finditer(FN_PARAM_REGEX_PATTERN_06, fn_body, re.DOTALL)
        params = _extract_and_validate_params_06(param_matches)

        # Create tool call
        tool_calls.append({"function": fn_name, "parameters": params})

    return tool_calls

def parse_xml_action_65(content: str) -> list:
    """
    """
    tool_calls = []
    # Find all function calls using regex pattern
    fn_matches = re.finditer(FN_REGEX_PATTERN_65, content, re.DOTALL)
    for fn_match in fn_matches:
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)
        # Parse parameters
        param_matches = re.finditer(FN_PARAM_REGEX_PATTERN_65, fn_body, re.DOTALL)
        params = _extract_and_validate_params_06(param_matches)

        # Create tool call
        tool_calls.append({"function": fn_name, "parameters": params})

    return tool_calls

if __name__ == '__main__':
    
    def replace_special_token(old_text):
        old_text = old_text.replace("<function=", "<function_never_used_51bce0c785ca2f68081bfa7d91973934=")
        old_text = old_text.replace("<parameter=", "<parameter_never_used_51bce0c785ca2f68081bfa7d91973934=")
        old_text = old_text.replace("</function>", "</function_never_used_51bce0c785ca2f68081bfa7d91973934>")
        old_text = old_text.replace("</parameter>", "</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>")
        old_text = old_text.replace("<seed:tool_call>", "<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>")
        old_text = old_text.replace("</seed:tool_call>", "</seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>")
        return old_text

    input_text = """用户现在需要我根据教案模板制作小学三年级的体育课教案，主题是跳绳，并且要放在/mnt/outputs目录下。首先，我需要先加载docx-filler这个技能，因为要处理Word模板填充。然后，可能需要先确认模板的情况，但用户没提供模板，不过可能需要先创建或者使用工具来处理。首先第一步应该是加载docx-filler技能，然后再进行后续的模板填充操作。所以先调用LoadSkill工具加载docx-filler技能。<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934><function_never_used_51bce0c785ca2f68081bfa7d91973934=LoadSkill><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=skill>docx-filler</parameter_never_used_51bce0c785ca2f68081bfa7d91973934></function_never_used_51bce0c785ca2f68081bfa7d91973934></seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>
"""

    tool_schemas = [
        {
        "type": "function",
        "name": "type",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Type content. If you want to submit your input, use \n at the end of content."
                }
            },
            "required": [
                "content"
            ]
        },
        "description": "Type content."
    }
    ]
    
    # input_text = replace_special_token(input_text)
    parsed_toolcalls = parse_xml_action_v02(input_text,)
    print(parsed_toolcalls)
    input()
    
    actions = []
    for xml_action in parsed_toolcalls:
        actions.append(
            {
                "action_type": xml_action["function"],
                "action_inputs": xml_action["parameters"]
            }
        )
    result = parsing_response_to_pyautogui_code(actions, 1080, 1920)
    print(result)
    json.dump(parsed_toolcalls, open("1.json", "w"), indent=4)
    import pdb;pdb.set_trace()
    
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
</function></parameter></function><function=click><parameter=point><point>123 638</point></parameter></functi><function=click><parameter=point><point>223 562</point></parameter></function></seed:tool_call>"""
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
    parsed_xml_actions = parse_xml_action_v5(content)
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
    