"""Deliver durable inputs between complete native runner steps."""
import asyncio

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
                return list(self.run_context.messages)
            try:
                pending = await inputs.deliver(
                    task_id, lambda text: self.run_context.messages.append(
                        Message(role="user", content=text)), snapshot)
            except TaskCancelled:
                raise asyncio.CancelledError() from None
            from ..services.task_recovery import reconciliation
            previous_unknown = reconciliation(inputs.tasks.events(task_id))
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
                         contexts, agent_hooks, inputs, task_id, wrapper, max_steps=128):
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
    kwargs = {}
    if tools and tools.get_tool("astrbot_file_read_tool"):
        kwargs = {"tool_result_overflow_dir": get_astrbot_system_tmp_path(),
                  "read_tool": tools.get_tool("astrbot_file_read_tool")}
    runner = task_runner(inputs, task_id)
    await runner.reset(provider=provider, request=request, run_context=wrapper,
                       tool_executor=FunctionToolExecutor(), agent_hooks=agent_hooks,
                       streaming=True, request_max_retries=1, **kwargs)
    async for _ in runner.step_until_done(max_steps):
        pass
    response = runner.get_final_llm_resp()
    if response is None:
        raise RuntimeError("子代理未返回结果")
    return response
