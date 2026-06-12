#!/usr/bin/env python3
"""
测试 Agnes AI API 是否连通
用法: python3 test-agnes-api.py --api-key YOUR_KEY
"""

import json
import argparse
import urllib.request
import urllib.error


def test_api(api_key, model="agnes-2.0-flash", max_tokens=256):
    url = "https://apihub.agnes-ai.com/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant. Keep responses concise."
            },
            {
                "role": "user",
                "content": "你好，请回复测试成功四个字即可。"
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                print("API 连通成功！")
                print("  模型:", result.get("model", "unknown"))
                print("  回复:", content)
                usage = result.get("usage", {})
                if usage:
                    print("  消耗: prompt={}, completion={}, total={}".format(
                        usage.get("prompt_tokens", "?"),
                        usage.get("completion_tokens", "?"),
                        usage.get("total_tokens", "?")
                    ))
                return True
            else:
                print("返回格式异常")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print("HTTP {}: {}".format(e.code, body))
        return False
    except Exception as e:
        print("请求失败: {}".format(e))
        return False


def test_image_api(api_key):
    url = "https://apihub.agnes-ai.com/v1/chat/completions"
    payload = json.dumps({
        "model": "agnes-2.0-flash",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图片里有什么？简短描述。"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://oces-public.oss-cn-shanghai.aliyuncs.com/images/logo.png"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 512,
        "temperature": 0.7
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key
        },
        method="POST"
    )

    print("\n--- 测试图像理解 ---")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                print("图像理解成功！")
                print("  回复:", content)
                return True
            else:
                print("返回格式异常")
                return False
    except Exception as e:
        print("图像理解失败: {}".format(e))
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="测试 Agnes AI API")
    parser.add_argument("--api-key", required=True, help="你的 API Key")
    parser.add_argument("--model", default="agnes-2.0-flash", help="模型名称")
    parser.add_argument("--no-image", action="store_true", help="跳过图像测试")
    args = parser.parse_args()

    print("=== Agnes AI API 连通性测试 ===")
    print("模型:", args.model)
    print()

    success = test_api(args.api_key, args.model)

    if not args.no_image:
        test_image_api(args.api_key)

    print("\n==============================")
    if success:
        print("所有测试通过！")
    else:
        print("测试未通过，请检查 API Key 或网络连接")
