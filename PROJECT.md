智能防误关麦助手 — 项目说明
目录
项目背景
整体架构
各模块职责
数据流转说明
版本迭代记录
文件目录结构
项目背景
问题来源
在远程会议、在线直播和网络授课等场景中，用户经常遭遇以下困境：

无意间点击了「静音」按钮，自己说话对方听不见
系统更新或软件崩溃后麦克风默认关闭，未被察觉
发言前短暂暂停，程序误判为「已退出」并关闭麦克风
上述问题的共同特征是：用户无法实时感知自身麦克风状态，导致信息传达中断。

解决方案
「智能防误关麦助手」在 Windows 后台静默运行，持续监听麦克风状态：

静音超过 30 秒（15s 计时 + 15s 缓冲）后弹窗提醒
检测到物理关麦后立即弹窗
通过语音识别 + 意图分析区分「误关麦」与「主动下线」，避免打扰用户
通过发言预测提前清零计时器，减少误报
整体架构
┌─────────────────────────────────────────────────────────┐
│                      main.py（入口 + CLI）               │
└─────────────────┬───────────────────────────────────────┘
                  │ 调用 / 管理
        ┌─────────┴─────────┐
        │                   │
   MicController         MicGuardian（核心守护器）
   （系统音量控制）        │
        │            ┌──────┼──────────────┐
        │          VAD    Alert          ASR + NLP
        │        (vad.py) (alert.py)  (asr.py + nlp.py)
        │            │                    │
        │         音频流                意图结果
        │            │       ┌───────────┘
        │         PreSpeech  │
        │        (pre_speech.py)
        │
        └─── config.py（全局参数，所有模块引入）
        └─── error_handler.py（全链路异常捕获）
        └─── stress_test.py（独立压力测试工具）
模块依赖关系
config.py          ← 被所有模块引入（无依赖）
error_handler.py   ← 被 guardian / stress_test 引入（依赖 config）
vad.py             ← 被 guardian 调用（依赖 config）
alert.py           ← 被 guardian 调用（无 config 依赖）
asr.py             ← 被 guardian 调用（依赖 config）
nlp.py             ← 被 guardian 调用（依赖 config）
pre_speech.py      ← 被 guardian 调用（依赖 config）
guardian.py        ← 被 main 调用（依赖以上所有）
stress_test.py     ← 独立工具（依赖 config / error_handler / guardian）
main.py            ← 程序入口（依赖 guardian）
各模块职责
config.py — 全局参数统一管理
职责	说明
音频参数	统一采样率（16000Hz）、帧长（30ms）、声道数、位深
VAD 阈值	底噪倍率、默认能量阈值、物理关麦帧数
缓冲区配置	VAD 滚动缓冲、ASR 截取时长、发言预测窗口
计时配置	静音阈值（15s）、延时等待（15s）、语义有效期（30s）
路径配置	项目目录、vosk 模型路径（支持环境变量覆盖）
参数校验	validate() 启动时校验参数一致性
error_handler.py — 全链路异常捕获
职责	说明
硬件异常	麦克风设备占用、断开、无可用设备
音频帧异常	帧解析错误，返回安全默认值 ("silence", 0.0)
ASR 异常	vosk 崩溃或空音频，返回空字符串
文件异常	规则库文件缺失，返回内置默认规则
依赖库异常	模块加载失败，打印安装提示
内存监控	定时检测进程 RSS，超过阈值时触发清理回调
全局兜底	install_global_handler() 捕获所有未处理异常
vad.py — 人声检测（VAD 短时能量算法）
职责	说明
底噪校准	采集 3 秒环境音频，计算平均短时能量作为底噪基准
逐帧检测	计算每帧振幅平方和，与动态阈值比较
状态输出	返回 "voice" / "silence" / "mic_off" 三种状态
滚动缓冲	deque 维护最近 5 秒 PCM 数据，供 ASR 截取
音频流管理	open() / close() 管理 PyAudio 流生命周期
alert.py — tkinter 置顶弹窗提醒
职责	说明
单例管理	AlertWindow._instance 进程级唯一实例
线程安全	所有 tkinter 操作通过 root.after(0, ...) 调度到 mainloop 线程
窗口复用	关闭时调用 withdraw() 隐藏，下次调用 deiconify() 复用
文案切换	StringVar 动态更新文案，支持静默超时 / 手动关麦两种类型
快捷键	Enter / Escape 均可关闭弹窗
asr.py — 离线语音识别
职责	说明
模型加载	加载 vosk 中文模型（路径来自 config.VOSK_MODEL_PATH）
可用性检测	available 属性，模型缺失时降级为不可用
PCM 识别	recognize_pcm(bytes) 接收原始 PCM 数据，返回识别文本
识别器复用	识别完成后重建 KaldiRecognizer（复用 model，不重新加载）
nlp.py — NLP 关键词意图匹配
职责	说明
规则加载	从 intent_rules.json 加载关键词规则库（支持 fallback 内置规则）
文本预处理	统一小写、去除标点，提升匹配鲁棒性
精确匹配	直接字符串包含判定
模糊匹配	difflib.SequenceMatcher 相似度 ≥ 0.65 则命中
意图分类	返回 {intent, label, action, score, matched_keyword}
意图分类与动作：

意图	触发关键词示例	action	业务效果
leave	先下线、退出会议	stop_remind	停止后续弹窗
inquiry	听得到吗、在吗	reset_timer	清零计时器
speak	我说一下、补充一点	reset_timer	清零计时器
foreshadow	接下来、顺带说下	mark_ready	标记即将发言
rest	我先静音、暂时不说	block_alert	屏蔽关麦弹窗
pre_speech.py — 发言预测
职责	说明
音频滑动窗口	维护最近 2 秒音频能量序列
预发声判定	能量在 [底噪×1.05, 底噪×2.5] 区间内判定为预发声
语义标记	NLP 命中 mark_ready 时记录时间戳
有效期管理	30 秒内语义标记有效，过期自动失效
联动输出	should_reset 为 True 时，guardian 立即清零计时器
guardian.py — 麦克风守护主控器
职责	说明
状态机管理	维护 silent_time / wait_flag / delay_cnt 三个核心状态
线程安全	_state_lock 保护所有状态变量的并发读写
全链路协调	整合 VAD → 发言预测 → 计时 → 延时 → ASR → NLP → 弹窗
关麦区分	区分误关麦（弹窗）与主动关麦（屏蔽）
容错恢复	连续帧错误 ≥ 20 次时调用 _restart_vad_stream() 重置流
内存监控	定时触发 check_memory_usage()，超限时清理音频缓冲
异常捕获	硬件 / ASR / 未知异常均有专项处理路径
stress_test.py — 72 小时稳定性压力测试工具
职责	说明
场景注入	ScenarioRunner 循环注入 5 类模拟场景
模拟 VAD	SimulatedVAD 在无真实麦克风时提供模拟音频数据
内存监控	每 5 分钟采样内存，记录峰值
精准度统计	统计应触发 / 实际触发 / 误报次数
报告生成	输出 stress_test_report.txt，问题追加到 issues.md
main.py — 程序入口 + CLI 交互
职责	说明
设备初始化	枚举音频设备，初始化 MicController
Guardian 启动	创建 MicGuardian，检测 ASR 可用状态
CLI 命令解析	处理 s/m/u/g/i/r/a/p/h/q 10 个命令
优雅退出	捕获 KeyboardInterrupt，停止 guardian 后退出
数据流转说明
完整处理链路
麦克风硬件
    │ PyAudio stream.read()
    ▼
VADDetector.detect_frame()
    │ 返回 (state, energy)
    ├─ state == "mic_off" → 手动关麦检测
    │       │
    │       ├─ had_voice_before_off AND NOT block_alert → show_mic_off_alert()
    │       └─ 主动关麦 / NLP block → 屏蔽弹窗
    │
    ├─ state == "voice" → 清零 silent_time，_voice_streak++
    │       │
    │       └─ PreSpeechDetector.update_audio(energy)
    │           → is_pre_voice / should_reset → _reset_cycle()
    │
    └─ state == "silence" → silent_time += frame_duration
            │
            ├─ silent_time >= 15s → wait_flag=True, delay_cnt=15s
            │
            └─ wait_flag AND delay_cnt <= 0
                    │
                    ▼
                _run_asr_nlp_pipeline()
                    │
                    ├─ show_silent_alert()       ← 弹窗提醒
                    │
                    ├─ VAD.get_recent_pcm(3s)   ← 截取音频
                    │
                    ├─ OfflineASR.recognize_pcm()  ← 语音识别
                    │
                    └─ IntentMatcher.match(text)   ← 意图匹配
                            │
                            ├─ stop_remind → _stop_remind=True
                            ├─ reset_timer → _reset_cycle()
                            ├─ mark_ready  → pre_speech.update_nlp()
                            └─ block_alert → _last_intent 记录
内存管理链路
guardian._listen_loop()
    │ 每 300s
    ▼
error_handler.check_memory_usage()
    │ psutil.rss > MEMORY_CLEAR_MB(300MB)
    ▼
guardian._clear_audio_cache()
    └─ vad._audio_buffer.clear()
异常处理链路
detect_frame() 抛出异常
    │
    ├─ OSError → handle_hardware_error() → _frame_error_count++
    │
    └─ 其他 → handle_audio_frame_error() → _frame_error_count++
                │
                └─ _frame_error_count >= 20
                        │
                        ▼
                    _restart_vad_stream()
                    vad.close() → sleep(0.2) → vad.open()
版本迭代记录
Phase 1：VAD 人声检测 + 基础弹窗（Week 10）
新增功能：

PyAudio 流式读取麦克风数据
短时能量 VAD 算法（底噪校准 + 动态阈值）
静默计时逻辑（silent_time 累加 / 归零）
15 秒延时等待缓冲（wait_flag + delay_cnt）
tkinter 置顶弹窗（静默超时 + 手动关麦两种场景）
Windows 音频 API 关麦状态检测（pycaw）
解决的 Bug：

T1-03 校准失败兜底 DEFAULT_ENERGY
首版弹窗 destroy() 重复创建问题（Phase 4 彻底修复）
Phase 2：离线 ASR + NLP 意图匹配（Week 11）
新增功能：

vosk 离线中文 ASR（不依赖网络）
弹窗触发时截取静音前 3 秒音频进行识别
intent_rules.json 关键词规则库（5 类意图）
模糊匹配（difflib，相似度阈值 0.65）
意图联动：stop_remind / reset_timer / mark_ready / block_alert
解决的 Bug：

T2-02 ASR 解析空音频 vosk 崩溃 → 主动检测空 bytes
T2-12 模糊匹配阈值过低误命中 → 提升至 0.65
T2-03 _reset_recognizer 重复加载模型 → 复用 model 对象
Phase 3：发言预测 + 动态关麦屏蔽（Week 12）
新增功能：

PreSpeechDetector：2 秒音频滑动窗口 + 预发声能量区间判定
NLP 语义历史：mark_ready 标记 30 秒有效期
发言预测联动：should_reset=True 时提前清零计时器
动态关麦区分：_had_voice_before_off + block_alert 判断
恢复机制：重新开麦后自动解除屏蔽
解决的 Bug：

T3-05 冷启动预发声误判 → 窗口帧数 < 50% 时跳过判定
T3-08 语义标记过期未失效 → 懒刷新机制
Phase 4：系统联调 + 全链路异常捕获 + 稳定性压测（Week 13）
新增功能：

config.py：统一全局参数（20+ 配置项），移除所有硬编码
error_handler.py：5 类异常捕获 + 全局兜底 + 内存监控
guardian.py 重构：_state_lock 线程锁、流重置、内存清理
alert.py 重构：进程级单例，withdraw/deiconify 复用
stress_test.py：72h 压力测试工具，含 SimulatedVAD 无硬件模式
文档新增：

README.md：使用文档
test_report.md：测试记录
PROJECT.md：本文档
issues.md：Bug 与优化跟踪
解决的 Bug：

各模块硬编码不一致 → config.py 统一管理
弹窗重复创建 BUG → alert.py 单例复用
全局状态并发竞态 → _state_lock 保护
硬件异常静默退出 → OSError 捕获 + 流重置
ASR 空音频崩溃 → error_handler.handle_asr_error
v4.1：ASR 语音验证 — 噪音过滤（用户反馈修复）
问题来源：用户反馈咳嗽、键盘敲击、走动等日常噪音能量超过阈值，被 VAD 误判为"人声"，导致静默计时器被错误重置，弹窗无法触发。

新增功能：

ASR 语音验证窗口：guardian.py 中 _verify_buffer + _in_verify_window 状态机
能量超限后收集 1.0s 音频送入 ASR 快速验证 asr.verify_speech()
ASR 识别出有效文本 → 确认人声 → 重置计时器
ASR 无结果 → 噪音过滤 → 静默计时继续累加
验证期间能量回落 → 放弃验证，清空缓冲
新增参数：

VOICE_VERIFY_ENABLED=True：开关（False 回退到 v4.0 纯能量检测）
VOICE_VERIFY_SECS=1.0：验证窗口时长
MIN_VOICE_TEXT_LEN=1：识别文本最小有效长度
Web 仪表盘同步更新：

VAD 波形图新增「ASR 语音验证中...」状态指示器（黄色闪烁 + 进度覆盖层）
新增「噪音已过滤 ✗」状态反馈
数据流链路更新：音频采集 → VAD 检测 → ASR 验证 → 计时器重置
验证测试：8 条测试用例（test_report.md T4-01 ~ T4-08）全部通过

v4.2：Bug 修复与 CLI 命令说明
问题来源：

l/listen 命令峰值显示 0 且无法停止
发言预测误判（用户未说话仍判定命中）
CLI 命令功能描述不清
修复内容：

main.py：listen 命令改用 PyAudio 直接读取麦克风流，RMS 动态条形图显示，按 Enter 停止
config.py：新增 PRE_VOICE_HIT_RATIO=1.5、MIN_FILL_RATIO=0.5、COOLDOWN=8
pre_speech.py：改进 _check_pre_voice() 算法，基于帧命中比率 + 窗口填充率检查 + 冷却期机制
新增详细命令说明文档
v4.3：彻底修复 listen 与发言预测误触发
问题来源：

l/listen 仍显示峰值 0 且无法停止：PyAudio stream.read() 阻塞模式在部分设备/环境下卡住
发言预测仍误触发：v4.2 只提高了阈值，但「静止噪音」（风扇/空调）持续落在预发声区间仍会命中
用户反馈弹窗容易被忽略，需要提示音
修复内容：

main.py：listen 命令改用 PyAudio callback 非阻塞模式：
彻底避免 stream.read() 阻塞导致无法停止的问题
添加设备可用性检测，无设备时给出友好提示
采集帧数统计，无数据时给出警告
config.py 发言预测参数全面收紧：
PRE_VOICE_LOWER_RATIO 1.5 → 2.0（缩小区间远离底噪）
PRE_VOICE_UPPER_RATIO 2.2 → 2.8（上限逼近 VAD threshold 3.0）
PRE_VOICE_HIT_RATIO 0.4 → 0.6（提高命中门槛）
PRE_VOICE_MIN_FILL_RATIO 0.5 → 0.75（要求窗口更满）
新增 PRE_VOICE_RISING_RATIO=1.2（能量上升斜率）
pre_speech.py：_check_pre_voice() 新增 能量上升斜率检查：
窗口后半段均值必须 ≥ 前半段 × 1.2
静止噪音（风扇/空调）能量分布均匀，不满足上升趋势
只有真正的气息渐强（pre-voice）才会触发
alert.py：弹窗时播放系统提示音（Windows winsound.MessageBeep），防止用户未看到弹窗而错过提醒
v5.2：麦克风关闭时彻底休眠，不再弹窗
用户反馈：希望在麦克风关闭（静音/关麦）状态下，系统完全不进行任何提醒；只有在麦克风开启时才正常检测和弹窗。

根本原因：旧版逻辑中，mic_off 检测分支虽然跳过了大部分 ASR/NLP 处理，但仍有以下问题：

误关麦检测逻辑（此前连续人声 → 弹窗提醒）在 mic_off 时仍然会触发 show_mic_off_alert()
发言预测器每帧仍在更新，麦克风关闭期间累积的能量窗口状态可能在恢复后导致误判
声学特征校准器每帧仍在 feed，可能采集到关闭期间的无效数据
修复内容：

guardian.py：_listen_loop 中 mic_off 处理重构：
state == "mic_off" 时，守护进入休眠模式：continue 前仅设置标志位并打印休眠信息
休眠期间：不调用 self._pre_speech.update_audio()、不调用 self._calibrator.feed_frame()、不调用任何弹窗函数
麦克风从 mic_off 恢复时：调用 self._pre_speech.reset() 清空累积状态，重置 _had_voice_before_off
移除旧版「误关麦弹窗」逻辑：show_mic_off_alert() 不再在 mic_off 分支调用
pre_speech.py：新增 reset() 方法，一键清空能量窗口、语义标记、触发冷却期等全部状态
v5.1：修复声学校准器安静环境误报
问题来源：用户在安静环境下未说话，声学基线校准却显示「均值=426.8，变异系数=8.111」，并提示「基线波动较大（CV > 0.5），建议在安静环境下重新校准」，与用户实际环境矛盾。

根本原因：

session_calibrator.py 在校准期间采集了所有帧的能量（包括静音帧），而安静环境中偶发的电磁脉冲/USB 电流声会导致少数帧 energy 极高，从而拉高均值、CV 极大。
提示信息未说明校准器采集的是**「发言基线」**而非「环境安静」基线，用户在安静环境下看到提示会感到困惑。
修复内容：

session_calibrator.py：feed_frame() 增加 is_voice: bool = False 参数，校准期间仅采集 VAD 判定为 voice 的帧，彻底避免静音帧污染基线。
session_calibrator.py：新增 _filter_outliers() 3σ 异常值过滤，在校准完成前自动去除偶发高能量样本。
session_calibrator.py：新增 _set_default_baseline() 默认基线机制；若校准期间未检测到足够有效发言（< 30 帧 voice），自动使用默认基线，并在后续检测到 voice 时自动触发重校准。
session_calibrator.py：_finish_calibration() 提示信息重写，区分「样本不足」「基线稳定（CV<0.15）」「基线一般（0.15~0.4）」「波动较大（0.4~1.0）」「波动极大（≥1.0）」五种场景，给出针对性的中文说明。
guardian.py：feed_frame(energy) → feed_frame(energy, is_voice=(state == "voice"))，将 VAD 状态传递给校准器。
v5.0：闲聊检测 — 区分会议发言与私下闲聊
问题来源：用户反馈会议中安静听讲时被频繁弹窗干扰。系统应判断「当前是否在聊与会议无关的内容」而非检测「静音时长」。需区分「会议发言」与「身边私下闲聊」。

解决方案：「动态会议白名单 + 音频能量 + 上下文语义」三重校验。

新增模块：

session_topic.py — 会议话题白名单管理器：
校准阶段（3 分钟）采集 ASR 文本，分词统计高频词
实时分类：命中白名单 → MEETING；命中闲聊词 → CHITCHAT；两边未命中 → NEUTRAL
内置 80+ 闲聊关键词库，支持手动追加/删除
session_calibrator.py — 声学特征基线校准器：
采集近距离开麦发言的能量均值与变异系数(CV)
实时评估音频稳定性：CV 低 = 近麦稳定发言（HIGH）；CV 高 = 声源偏移/小声闲聊（LOW）
核心逻辑变更（guardian.py）：

旧逻辑：静音 ≥15s → 延时等待 15s → 弹窗
新逻辑：VAD 检测到 voice → ASR 语音验证 → topic 分类：
MEETING → 重置闲聊计时，不提醒
CHITCHAT → 累加闲聊计时，≥3s 弹窗
NEUTRAL → 不重置也不累加
连续非闲聊 ≥5s → 重置闲聊计时
新增参数（config.py）：

闲聊检测：CHITCHAT_ALERT_SECS=3、CHITCHAT_RESET_SECS=5
话题提取：TOPIC_CALIBRATE_SECS=180、TOPIC_TOP_N=50、TOPIC_MIN_FREQ=2 等
声学基线：ACOUSTIC_CALIBRATE_FRAMES、ACOUSTIC_NEAR_ENERGY_MIN、ACOUSTIC_NEAR_STABLE_RATIO、ACOUSTIC_CHIT_VOLATILE_RATIO
Web 仪表盘同步更新：

新增 4 个可视化组件：ChitchatDetector、TopicWhitelistPanel、AcousticScorePanel
数据流链路更新：音频采集 → VAD 检测 → ASR 识别 → 话题分类 → 声学评分 → 闲聊计时 → 弹窗提醒
新增模块 session_topic、session_calibrator 到模块状态卡片
文档更新：

README.md：功能概述、CLI 命令、配置参数、版本日志全面更新
test_report.md：新增 T5 系列测试用例
文件目录结构
smart-mic-guardian/
│
├── main.py               # 程序入口，CLI 交互界面
├── guardian.py           # 麦克风守护主控器（核心，v5.0 闲聊检测）
├── vad.py                # 人声检测模块（VAD 短时能量）
├── alert.py              # tkinter 弹窗提醒（单例复用，v5.0 支持自定义文案）
├── asr.py                # 离线语音识别（vosk）
├── nlp.py                # NLP 意图匹配
├── pre_speech.py         # 发言预测模块
│
├── session_topic.py      # 会议话题白名单管理器（v5.0 新增）
├── session_calibrator.py # 声学特征基线校准器（v5.0 新增）
│
├── config.py             # 全局参数统一配置（v5.0 新增 12 项参数）
├── error_handler.py      # 全链路异常捕获
├── stress_test.py        # 72 小时稳定性压力测试工具
├── test_mic.py           # 麦克风快速测试脚本
│
├── intent_rules.json     # NLP 关键词规则库
├── requirements.txt      # Python 依赖列表
│
├── model/                # vosk 中文模型目录（用户自行下载）
│   ├── am/
│   ├── conf/
│   └── ...
│
├── README.md             # 使用文档（安装/CLI/配置/FAQ）
├── test_report.md        # 测试记录（Phase 1-4）
├── PROJECT.md            # 本文档（架构/模块/数据流/迭代）
├── issues.md             # Bug 与优化跟踪
├── GITHUB_SETUP.md       # GitHub 仓库配置说明
│
└── stress_test_report.txt  # 压力测试报告（自动生成）