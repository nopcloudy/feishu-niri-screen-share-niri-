# 架构与接口

## 模块划分

| 模块 | 输入 | 输出 | 职责 |
| --- | --- | --- | --- |
| niri | 当前显示器画面 | `wlr-screencopy` 帧 | 提供 Wayland 合成后的桌面内容 |
| `feishu_niri_share/backend/` | Portal `ScreenCast` 请求、显示器名 | PipeWire 视频节点 ID | 选择显示器、取帧、发布视频流 |
| 飞书客户端 | Portal 会话和 PipeWire 节点 | 飞书会议共享视频 | 接收帧，并使用飞书自身协议发送 |
| `feishu_niri_share/cli.py` | `install/status/uninstall` 命令参数 | 用户配置、构建产物、安装状态 | 部署、检查、回退，不接触会议数据 |

## 控制流与数据流

飞书通过 `org.freedesktop.portal.ScreenCast` 发出请求。`xdg-desktop-portal` 读取当前 niri 会话的 `niri-portals.conf`，把 ScreenCast 请求交给 `wlr` 后端。后端按 `output_name` 选显示器，通过 `wlr-screencopy` 获取帧，创建 PipeWire 节点并返回节点 ID。飞书从该节点读取原始视频，之后的编码、网络传输、会议权限和显示均由飞书负责。鼠标键盘输入不在此链路中。

配置中的 `chooser_type=none` 使后端直接选择已声明的显示器。`max_fps` 限制取帧频率。后端与飞书通过标准 Portal/PipeWire 接口耦合，因此不需要调用飞书私有 API。

## 管理命令接口

`install --output NAME [--fps N]`：`NAME` 为 niri 输出名，如 `eDP-1`；`N` 范围 1–120，默认 30。先编译后端，再备份原文件、安装、写配置并重启用户 Portal。构建失败时不会改动共享配置；安装中途失败会恢复备份。已有安装记录时拒绝重复安装，防止覆盖原始备份。

`status`：只读检查后端文件、Portal 路由和采集配置。不登录飞书，也不启动会议。

`uninstall`：读取安装记录，比较当前文件与安装时的 SHA-256；未变更的文件恢复原状，安装后又改过的文件保留并报告。最后重启用户 Portal。

状态目录遵循 `XDG_STATE_HOME`，默认 `~/.local/state/feishu-niri-share`。构建目录遵循 `XDG_CACHE_HOME`，默认 `~/.cache/feishu-niri-share/build`。配置目录遵循 `XDG_CONFIG_HOME`，默认 `~/.config`。

## 与 UU 项目的关系

UU 主机自行取帧、编码，并经 UU 信令/WebRTC 发送，还处理远程输入。这里复用的是“在 niri 上从 Wayland 合成器取帧”的架构思路；视频发送保留在飞书客户端内。完整后端采用独立的 MIT 许可上游实现，未复制 GPL 许可的 UU 源码。

## 飞书内置远程控制的接入条件

远程控制与现有屏幕共享是两条不同的数据流。若飞书 Linux 客户端开放该能力，控制链路应为：

```text
对端飞书鼠标/键盘操作
  → 飞书会议的授权与控制信令
  → 本机飞书客户端接收控制事件
  → 飞书公开或受支持的事件接口
  → niri/Wayland 输入后端
```

输入后端至少需要接收“会话开始/结束、操作者身份、指针坐标或位移、按键状态、键码”等信息；会话结束时须释放仍处于按下状态的按键。对 niri 的输入注入只能实现最后一步，无法代替飞书负责的授权、信令、事件接收和界面。当前项目没有上述飞书事件接口，因此没有添加一个无法接收真实会议事件的假输入后端。

核查记录（2026-10-21）：

- 飞书[官方屏幕共享帮助](https://www.feishu.cn/hc/en-US/articles/360046586533-share-your-screen)明确称 Linux 设备不支持远程控制，Linux 上相关选项不可用。
- 本机 Flatpak `cn.feishu.Feishu` 7.72.23 的 `libbyteview-bytertc.so` 包含 `GetRemoteControlEngine` 等导出符号，也包含 `RemoteControlEngineFake` 类型。检查 `GetRemoteControlEngine` 的构造路径及对象虚表后，可见多个虚函数只返回、不处理输入。这是该安装包存在占位实现的证据；不能仅凭导出函数名推断 Linux 端已实现远程控制。
- 该检查是本机二进制的静态分析，没有第二台桌面设备参与飞书会议的端到端控制测试。未来客户端版本变化时，应重新核查菜单行为和事件接口。

如果飞书将来支持 Linux，首先验证对端能在飞书界面申请控制、共享端能批准、客户端能收到输入事件；然后再选择 niri 可用的输入后端并实现坐标变换和按键生命周期。该后端的接口应与现有 ScreenCast 采集后端分离，不改变视频共享的数据流。
