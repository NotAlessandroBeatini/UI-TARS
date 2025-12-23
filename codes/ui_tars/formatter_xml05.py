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
        xml_parts = [f"\n{indent}<object>"]
        for key, value in param_value.items():
            converted_value = convert_param_value_to_xml(value, indent_level + 2)
            if _is_complex_value(converted_value):
                # 复杂值（包含换行或嵌套结构）
                xml_parts.append(
                    f"{indent}<parameter={key}>{converted_value}{indent}</parameter>"
                )
            else:
                # 简单值
                xml_parts.append(
                    f"{indent}<parameter={key}>{converted_value}</parameter>"
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
            return f"{indent}<text_never_used_51bce0c785ca2f68081bfa7d91973934>{_indent_text(str_value, indent_level + 1)}{indent}</text_never_used_51bce0c785ca2f68081bfa7d91973934>"

        # 检查是否为多行文本
        if "\n" in str_value:
            return f"\n{_indent_text(str_value, indent_level)}"

        return str_value


def _is_complex_value(value_str):
    """检查值是否为复杂值（包含换行或XML标签）"""
    return "\n" in value_str or value_str.strip().startswith("<")


def _contains_xml_tags(text):
    """检查文本是否包含XML标签或可能干扰XML解析的字符"""
    patterns = [
        "<function=",
        "</function>",
        "<parameter=",
        "</parameter>",
        "<seed:tool_call>",
        "</seed:tool_call>",
    ]
    for pattern in patterns:
        if pattern in text:
            return True
    return False


def _indent_text(text, indent_level):
    """为多行文本添加缩进"""
    indent = "    " * indent_level
    lines = text.split("\n")
    return "".join(f"{indent}{line}" if line.strip() else "" for line in lines)


def parse_xml_param_value(xml_content):
    """
    从XML格式解析参数值回JSON格式

    Args:
        xml_content: XML格式的参数内容

    Returns:
        解析后的Python值（dict/list/str/etc）
    """

    # 检查是否以换行符开头（我们的多行文本格式）
    if xml_content.startswith("\n"):
        return _normalize_indented_text(xml_content[1:])

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
            return _normalize_indented_text(content)

    # 处理多行文本（检查是否有缩进）
    if "\n" in xml_content:
        return _normalize_indented_text(xml_content)

    # 处理简单文本
    return xml_content


def _normalize_indented_text(text):
    """标准化缩进的文本，移除公共缩进"""
    lines = text.split("\n")

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
            normalized_lines.append(
                line[min_indent:] if len(line) >= min_indent else line
            )
        else:  # 空行
            normalized_lines.append("")

    return "".join(normalized_lines)


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

    function_block = f"<function={function_name}>"
    for param_name, param_value in parameters.items():
        # 使用新的XML转换逻辑处理参数值
        xml_value = convert_param_value_to_xml(param_value, indent_level=0)
        xml_value = _normalize_indented_text(xml_value)
        function_block += f"<parameter={param_name}>{xml_value}</parameter>"

    function_block += "</function>"
    return "<seed:tool_call>\n" + function_block + "\n</seed:tool_call>"


def is_anyof_oneof_allof(schema):
    """
    检查 properties 是否包含 anyOf/oneOf/allOf 类型的参数。

    :param properties: dict, 包含参数定义的字典
    :return: bool, 如果包含 anyOf/oneOf/allOf 类型的参数返回 True，否则返回 False
    """
    schema_str = json.dumps(schema, ensure_ascii=False)
    for case in ["$ref", "anyOf", "oneOf", "allOf"]:
        if case in schema_str:
            return True
    return False


def contains_list_or_nested_object(schema):
    """
    判断 OpenAI Function Call 的 tool schema 是否包含 list 参数类型或嵌套的 object。

    :param schema: dict, OpenAI Function Call 的工具 schema
    :return: bool, 如果包含 list 或嵌套 object 返回 True，否则返回 False
    """

    def check_property(properties):
        """
        递归检查 properties 是否包含 list 或嵌套 object
        """
        properties_count = 0
        for key, value in properties.items():
            param_type = value.get("type")
            if param_type == "array":  # 如果是 list 类型
                return True
            if param_type == "object":  # 如果是 object 类型
                # 递归检查嵌套的 object
                if "properties" in value:
                    properties_count += 1
        return properties_count

    # 获取 schema 的根级 properties
    root_properties = schema["parameters"].get("properties", {})
    properties_count = check_property(root_properties)
    return properties_count > 0


if __name__ == "__main__":
    # 测试工具调用
    test_calls = {
        "id": "call_123",
        "type": "function",
        "function": {
            "name": "doubao_code_interpreter",
            "arguments": {
                "code": [{"name": "code", "value": "print('Hello, World!')"}]
            },
        },
    }

    response = make_assistant_response(test_calls)
    print(response)
