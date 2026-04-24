# 说明

## 关于homeassitant

基本上是用codex重构的，里面可能有很多是无用的测试代码（大致原因是测试命令本身可运行，但当前环境对系统临时目录无权限，一直没处理）

里面有README.md和其他的文档。应该用ai编程工具就能了解大致是怎样的，写出来的代码我也不太看得明白，有点高级。

主要是实现了：

- 模拟homeassitant api，[REST API | Home Assistant Developer Docs](https://developers.home-assistant.io/docs/api/rest/) 。（但是homeassitant 的api是以实体为单位操作的，并没有提供设备-实体的映射）
- 能够按需要，用llm生成多个伪造的符合homeassitant实体，封装成隔离的测试环境。（比如测试环境一，有一卧室一客厅，有空调、温度传感器什么的）。【这个只是简单测试过api，只是试着实现了下，不知道生成的环境会不会有问题】

## 关于agent

base：

- filter：从设备列表中筛选出满足本次指令的候选设备集。
- planner：可以依据偏好、场景等，规划出各设备如何调用。
  - executor：依据计划表，对各设备执行读状态、执行动作、持久化监控
    - 因为一个设备包含多个实体，所以在读状态、执行动作、持久化监控前会挑选出合适的实体来执行指令

> 需要说明的是，原生的homeassitant api只有以实体为单位。想要知道设备及设备与实体的映射关系，需要借助设备注册表和实体注册表来实现。在剩下的两个实验中，感觉这也不是很关键，所以暂只实现了原生的homeassitant api。
>
> 另外，筛选候选设备集之前有两个实现思路，一种直接在测试时给设备的name加上位置（比如床边的灯），另一种则是根据对话记忆来筛选（比如用户曾告知床边的灯是light.xxx）。

privacy_：

- 利用langchain提供的middleware机制，在调用llm前后对内容进行编码和解码，主要原理是处理messages列表和调用工具的入参。
- 但是encode_text和decode_text暂未落地，还是测试的demo实现。

retryValidate：

- 目前是在base_agent执行完指令后，再构建一个检验任务复用base去检测。

