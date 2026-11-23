# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""In-process access to the DevOps Agent CLI functions in devops-agent-chat.py.

The CLI file name contains a hyphen, so it cannot be imported with a plain `import`.
Loading it here lets the status page and the KiroCrew bridge call its functions directly
instead of spawning a child process.
"""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).with_name("devops-agent-chat.py")
_SPEC = importlib.util.spec_from_file_location("devops_agent_chat", _PATH)
chat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chat)

AgentError = chat.AgentError
