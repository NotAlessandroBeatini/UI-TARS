import re
import json


def convert_param_value_to_xml(param_value, indent_level=0):
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
        if len(param_value) == 1 and "_xml_content_" in param_value:
            # 特殊处理XML内容
            xml_content = param_value["_xml_content_"]
            return f"{indent}<text_never_used_51bce0c785ca2f68081bfa7d91973934>{indent}{xml_content}{indent}</text_never_used_51bce0c785ca2f68081bfa7d91973934>"

        # 对于普通字典类型，转换为嵌套的XML格式
        xml_parts = [f"{indent}<object>"]
        for key, value in param_value.items():
            converted_value = convert_param_value_to_xml(value, indent_level + 2)
            if _is_complex_value(converted_value):
                # 复杂值（包含换行或嵌套结构）
                xml_parts.append(
                    f"{indent}<parameter_never_used_51bce0c785ca2f68081bfa7d91973934={key}>{converted_value}{indent}</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>"
                )
            else:
                # 简单值
                xml_parts.append(
                    f"{indent}<parameter_never_used_51bce0c785ca2f68081bfa7d91973934={key}>{converted_value}</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>"
                )
        xml_parts.append(f"{indent}</object>")
        return "".join(xml_parts)

    elif isinstance(param_value, list):
        # 对于列表类型，转换为包含多个元素的XML格式
        if len(param_value) == 0:
            return f"{indent}<list>{indent}</list>"

        xml_parts = [f"{indent}<list>"]
        for item in param_value:
            converted_item = convert_param_value_to_xml(item, indent_level + 2)
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
            return f"{indent}<text_never_used_51bce0c785ca2f68081bfa7d91973934>{str_value, indent_level + 1}{indent}</text_never_used_51bce0c785ca2f68081bfa7d91973934>"

        return str_value


def _is_complex_value(value_str):
    """检查值是否为复杂值（包含换行或XML标签）"""
    return "\n" in value_str or value_str.strip().startswith("<")


def _contains_xml_tags(text):
    """检查文本是否包含XML标签或可能干扰XML解析的字符"""
    patterns = [
        "<function_never_used_51bce0c785ca2f68081bfa7d91973934=",
        "</function_never_used_51bce0c785ca2f68081bfa7d91973934>",
        "<parameter_never_used_51bce0c785ca2f68081bfa7d91973934=",
        "</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>",
        "<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934=",
        "</seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>",
    ]
    for pattern in patterns:
        if pattern in text:
            return True
    return False

def parse_xml_param_value(xml_content):
    """
    从XML格式解析参数值回JSON格式

    Args:
        xml_content: XML格式的参数内容

    Returns:
        解析后的Python值（dict/list/str/etc）
    """

    # 否则正常处理
    xml_content = xml_content.strip()

    # 处理 <object> 标签
    if xml_content.startswith("<object>") and xml_content.endswith("</object>"):
        return _parse_xml_object(xml_content)

    # 处理 <list> 标签
    elif xml_content.startswith("<list>") and xml_content.endswith("</list>"):
        return _parse_xml_list(xml_content)

    # 处理 <text_never_used_51bce0c785ca2f68081bfa7d91973934> 标签
    elif "<text_never_used_51bce0c785ca2f68081bfa7d91973934>" in xml_content:
        text_pattern = r"<text_never_used_51bce0c785ca2f68081bfa7d91973934>(.*?)</text_never_used_51bce0c785ca2f68081bfa7d91973934>"
        match = re.search(text_pattern, xml_content, re.DOTALL)
        if match:
            content = match.group(1)
            # 移除前后空行和统一缩进
            return content

    # 处理简单文本
    return xml_content

def _parse_xml_object(xml_content):
    """解析XML object标签"""

    # 提取object内容
    content = xml_content[8:-9]  # 移除<object>和</object>
    result = {}

    # 查找所有parameter标签
    param_pattern = r"<parameter=([^>]+)>(.*?)</parameter>"
    matches = re.findall(param_pattern, content, re.DOTALL)

    for param_name, param_content in matches:
        result[param_name] = parse_xml_param_value(param_content.strip())

    return result


def _parse_xml_list(xml_content):
    """解析XML list标签"""

    # 提取list内容
    content = xml_content[6:-7]  # 移除<list>和</list>
    result = []

    # 查找所有item标签
    item_pattern = r"<item>(.*?)</item>"
    matches = re.findall(item_pattern, content, re.DOTALL)

    for item_content in matches:
        result.append(parse_xml_param_value(item_content.strip()))

    return result


def make_assistant_response(call):
    # 构建新格式的工具调用
    # 检查是否是OpenAI格式 (有id, type, function字段)
    function_name = call["function"]["name"]
    parameters = call["function"].get("arguments", {})

    # 处理parameters可能是JSON字符串的情况
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Error when converting tool call to assistant response: json.loads parameters error: {e}"
            )

    elif not isinstance(parameters, dict) and not isinstance(parameters, list):
        raise ValueError(
            f"Error when converting tool call to assistant response: parameters must be a dict or list, but got {type(parameters)}"
        )

    # 特殊情况，处理doubao_code_interpreter (支持多种函数名格式)

    function_block = (
        f"<function_never_used_51bce0c785ca2f68081bfa7d91973934={function_name}>"
    )
    
    for param_name, param_value in parameters.items():
        # 使用新的XML转换逻辑处理参数值
        xml_value = convert_param_value_to_xml(param_value, indent_level=0)
        function_block += f"<parameter_never_used_51bce0c785ca2f68081bfa7d91973934={param_name}>{xml_value}</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>"

    function_block += "</function_never_used_51bce0c785ca2f68081bfa7d91973934>"
    return (
        "<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>"
        + function_block
        + "</seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>"
    )

def make_assistant_responses(calls):
    # 构建新格式的工具调用
    # 检查是否是OpenAI格式 (有id, type, function字段)
    function_block = ""
    for call in calls:
        function_name = call["function"]["name"]
        parameters = call["function"].get("arguments", {})

        # 处理parameters可能是JSON字符串的情况
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Error when converting tool call to assistant response: json.loads parameters error: {e}"
                )

        elif not isinstance(parameters, dict) and not isinstance(parameters, list):
            raise ValueError(
                f"Error when converting tool call to assistant response: parameters must be a dict or list, but got {type(parameters)}"
            )

        # 特殊情况，处理doubao_code_interpreter (支持多种函数名格式)

        function_block += (
            f"<function_never_used_51bce0c785ca2f68081bfa7d91973934={function_name}>"
        )
        
        for param_name, param_value in parameters.items():
            # 使用新的XML转换逻辑处理参数值
            xml_value = convert_param_value_to_xml(param_value, indent_level=0)
            function_block += f"<parameter_never_used_51bce0c785ca2f68081bfa7d91973934={param_name}>{xml_value}</parameter_never_used_51bce0c785ca2f68081bfa7d91973934>"

        function_block += "</function_never_used_51bce0c785ca2f68081bfa7d91973934>"
    return (
        "<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>"
        + function_block
        + "</seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>"
    )
    

if __name__ == "__main__":
    
    tool_schemas = [
        {
            "type": "function",
            "name": "doubao_code_interpreter",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                    "type": "object",
                    "properties": {
                        "name": {
                        "type": "string"
                        },
                        "value": {
                        "type": "string"
                        }
                    },
                    "required": ["name", "value"]
                    },
                    "language": {
                    "type": "boolean"
                    },
                    "index": {
                    "type": "integer"
                    },
                    "text": {
                    "type": "string"
                    },
                    "chunks": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                    }
                },
                "required": ["code", "language", "index", "text", "chunks"]
            },
            "description": "Type content."
        }
    ]
    # 测试工具调用
    test_calls = [
        {
            "id": "call_123",
            "type": "function",
            "function": {
                "name": "doubao_code_interpreter",
                "arguments": {
                    "code": {"name": "code", "value": "print('Hello,\n\n\\n World!')"},
                    "language": True,
                    "index": 1,
                    "text": "this is a test",
                    "chunks": []
                },
            }
        }
    ]

    response = make_assistant_responses(test_calls)
    print(response)

    from action_parser import parse_xml_action_04
    
    tool_schemas = [
        {
            "type": "function",
            "name": "doubao_code_interpreter",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                    "type": "object",
                    "properties": {
                        "name": {
                        "type": "string"
                        },
                        "value": {
                        "type": "string"
                        }
                    },
                    "required": ["name", "value"]
                    },
                    "language": {
                    "type": "boolean"
                    },
                    "index": {
                    "type": "integer"
                    },
                    "text": {
                    "type": "string"
                    },
                    "chunks": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                    }
                },
                "required": ["code", "language", "index", "text", "chunks"]
            },
            "description": "Type content."
        }
    ]
    
    response = '''<seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934><function_never_used_51bce0c785ca2f68081bfa7d91973934=doubao_code_interpreter><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=code><object><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=name>code</parameter_never_used_51bce0c785ca2f68081bfa7d91973934><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=value>print('Hello,

\\n World!')</parameter_never_used_51bce0c785ca2f68081bfa7d91973934></object></parameter_never_used_51bce0c785ca2f68081bfa7d91973934><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=language>True</parameter_never_used_51bce0c785ca2f68081bfa7d91973934><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=index>1</parameter_never_used_51bce0c785ca2f68081bfa7d91973934><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=text>this is a test</parameter_never_used_51bce0c785ca2f68081bfa7d91973934><parameter_never_used_51bce0c785ca2f68081bfa7d91973934=chunks><list><item>block1</item><item>block2</item></list></parameter_never_used_51bce0c785ca2f68081bfa7d91973934></function_never_used_51bce0c785ca2f68081bfa7d91973934></seed:tool_call_never_used_51bce0c785ca2f68081bfa7d91973934>'''
    tool_calls = parse_xml_action_04(response, tool_schemas)
    test_calls = [{"type": "function", "function": {"name": call["function"], "arguments": call["parameters"]}} for call in tool_calls]
    print(test_calls)