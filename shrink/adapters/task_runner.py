"""Deliver durable inputs between complete native runner steps."""
import asyncio
import json

from ..services.task_errors import TaskFailure

from ..services.task_inputs import TaskCancelled


def task_runner(inputs, task_id):
    from astrbot.core.agent.runners.base import AgentState
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.agent.message import Message

    class TaskRunner(ToolLoopAgentRunner):
        async def _iter_llm_responses_with_fallback(self):
            # The durable scheduler owns retry/fallback budgets for background tasks.
            self.provider._task_config_prepared = False
            async for response in self._iter_llm_responses():
                if not response.is_chunk:
                    if response.role == "err":
                        raise TaskFailure("unavailable", response.completion_text)
                    self._sanitize_malformed_tool_calls(response)
                    if not response.tools_call_name and not (response.completion_text or "").strip():
                        raise TaskFailure("output_format", "子代理返回空结果")
                    for name in response.tools_call_name or []:
                        if not self.req.func_tool or not self.req.func_tool.get_tool(name):
                            raise TaskFailure("invalid_request", "工具不存在：" + name)
                yield response

        async def step(self):
            def snapshot():
                images = getattr(self, "images", None)
                return images.compact(self.run_context.messages) if images else list(self.run_context.messages)
            try:
                pending = await inputs.deliver(
                    task_id, lambda text: self.run_context.messages.append(
                        Message(role="user", content=text)), snapshot)
            except TaskCancelled:
                raise asyncio.CancelledError() from None
            from ..services.task_recovery import reconciliation
            previous_unknown = reconciliation(inputs.tasks.events(task_id))
            images = getattr(self, "images", None)
            if images:
                restored = await images.resolve(images.compact(self.run_context.messages),
                                                modalities=self.provider.provider_config.get("modalities"),
                                                max_age=None)
                self.run_context.messages[:] = [Message.model_validate(m) for m in restored]
            responded = False
            async for response in super().step():
                responded = responded or response.type == "agent_stats"
                yield response
            if not responded or self.state == AgentState.ERROR or self.was_aborted():
                return
            unknown = reconciliation(inputs.tasks.events(task_id))
            if unknown and unknown != previous_unknown:
                raise TaskFailure("unknown_write", "工具已开始但没有结束回执，先核对副作用")
            await inputs.processed(task_id, snapshot(), pending)
            if self.done() and inputs.pending(task_id):
                # Input can arrive during the final model call, which has no tool boundary.
                self._transition_state(AgentState.RUNNING)

    return TaskRunner()


async def run_task_agent(context, *, event, chat_provider_id, prompt, system_prompt, tools,
                         contexts, agent_hooks, inputs, task_id, wrapper, max_steps=128, images=None):
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.tools.computer_tools.util import (
        LOCAL_NETWORK_POLICY_NOTICE, get_local_permission_policy, is_local_runtime)
    from astrbot.core.utils.astrbot_path import get_astrbot_system_tmp_path

    provider = await context.provider_manager.get_provider_by_id(chat_provider_id)
    if provider is None:
        raise ValueError("任务模型不可用")
    from .task_provider import resume_contexts, task_provider
    provider = task_provider(provider)
    # Keep the native Context.tool_loop_agent local-execution policy and overflow behavior.
    if (tools and any(tools.get_tool(name) and tools.get_tool(name).active for name in
                      ("astrbot_execute_shell", "astrbot_shell_session", "astrbot_execute_python"))
            and is_local_runtime(wrapper)):
        policy = get_local_permission_policy(wrapper)
        if policy.allow_execution and not policy.allow_network:
            system_prompt += "\n" + LOCAL_NETWORK_POLICY_NOTICE
    saved = inputs.repository.context(task_id)
    if saved is not None:
        saved = resume_contexts(saved, provider)
    request = ProviderRequest(
        prompt=None if saved is not None else prompt,
        system_prompt="" if saved is not None else system_prompt,
        contexts=saved if saved is not None else list(contexts), func_tool=tools)
    if images and saved is None:
        try:
            material = json.loads(prompt)
        except (ValueError, TypeError):
            material = {}
        refs = material.get("image_refs", []) if isinstance(material, dict) else []
        if refs:
            request.contexts.append({"role": "user", "content": [
                {"type": "text", "text": "[本任务明确引用的原图]"},
                *[{"type": "image_url", "image_url": {"url": ref}} for ref in refs
                  if isinstance(ref, str) and ref.startswith("attachment:")]]})
    kwargs = {}
    if tools and tools.get_tool("astrbot_file_read_tool"):
        kwargs = {"tool_result_overflow_dir": get_astrbot_system_tmp_path(),
                  "read_tool": tools.get_tool("astrbot_file_read_tool")}
    runner = task_runner(inputs, task_id)
    runner.images = images
    if tools and tools.get_tool("shrink_orchestrate"):
        from astrbot.core.agent.tool import ToolSet
        allowed = {tool.name: tool for tool in tools.tools if tool.active}
        runner.available_tools = [{"name": tool.name, "description": tool.description or "",
                                   "parameters": tool.parameters} for tool in allowed.values()]
        loaded = ToolSet([allowed["shrink_orchestrate"]])
        # Restore only previously used tools, preserving persisted call/result contexts.
        for message in saved or []:
            for call in message.get("tool_calls", []):
                name = call.get("function", {}).get("name")
                if name in allowed and not loaded.get_tool(name):
                    loaded.add_tool(allowed[name])
        request.func_tool = loaded
        def load_tool(name):
            if name not in allowed:
                raise ValueError("当前任务没有这个工具")
            if not runner.req.func_tool.get_tool(name):
                runner.req.func_tool.add_tool(allowed[name])
            raw = getattr(runner, "_skill_like_raw_tool_set", None)
            if raw is not None and not raw.get_tool(name):
                raw.add_tool(allowed[name])
        runner.load_tool = load_tool
        guidance = ("\n工具按需加载：先shrink_orchestrate tools按query查短目录；"
                    "再tools传name读取该工具参数并启用，下一轮即可直接调用。"
                    "执行任务所需技能也通过已启用的技能/文件工具按需读取。")
        if saved is None:
            request.system_prompt += guidance
        else:
            request.contexts = [*request.contexts, {"role": "user", "content": guidance}]
    await runner.reset(provider=provider, request=request, run_context=wrapper,
                       tool_executor=FunctionToolExecutor(), agent_hooks=agent_hooks,
                       streaming=True, request_max_retries=1, **kwargs)
    runners = event.get_extra("shrink_task_runners") or {}
    event.set_extra("shrink_task_runners", runners)
    runners[task_id] = runner
    try:
        async for _ in runner.step_until_done(max_steps):
            pass
    finally:
        runners.pop(task_id, None)
    response = runner.get_final_llm_resp()
    if response is None:
        raise RuntimeError("子代理未返回结果")
    return response
