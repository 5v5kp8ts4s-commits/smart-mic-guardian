智能防误关麦助手
一款运行在 Windows 桌面的轻量级麦克风守护工具。
在会议/直播/录课等场景中，自动检测麦克风是否静音过长，智能弹窗提醒，避免错过发言。

目录
功能概述
环境要求
安装与启动
CLI 命令说明
配置说明
常见问题
功能概述
模块	说明
VAD 人声检测	基于短时能量算法，实时区分人声与环境噪音；启动时自动校准底噪阈值
话题白名单	开场前 3 分钟自动提取会议高频专业词，构建本场专属白名单（v5.0）
闲聊检测	ASR 语音实时识别 → 与会议白名单匹配；命中闲聊词库则累计计时，达 3 秒弹窗提醒（v5.0）
声学特征辅助	变异系数(CV)评估音频稳定性，辅助区分「近距离开麦发言」与「扭头小声闲聊」（v5.0）
延时等待	静音满 15 秒后进入 15 秒缓冲期，缓冲期内恢复人声则取消提醒
弹窗提醒	tkinter 置顶弹窗，单例复用，关闭后不销毁，下次直接复用
手动关麦检测	识别全零音频帧（物理关麦），区分「误关麦」与「主动关麦」并分策略处理
离线 ASR	使用 vosk 离线模型，弹窗触发时截取静音前 3 秒音频进行语音识别
NLP 意图匹配	基于关键词规则库（intent_rules.json），识别 5 类意图并调整提醒策略
发言预测	检测预发声音频特征 + NLP 语义历史，提前清零计时器，减少误报
v5.0 闲聊检测三重校验
话题白名单匹配：开场 3 分钟自动采集高频词。只要后续说话内容包含白名单词 → 判定为会议发言，不告警。
闲聊词库过滤：内置 80+ 生活化闲聊词（饮食/购物/娱乐/亲友），白名单未命中 + 命中闲聊词 → 累计闲聊计时。
声学特征辅助：CV 变异系数区分「近麦稳定发言」与「声源偏移小声闲聊」。
持续时长过滤：连续 ≥3 秒闲聊才弹窗，偶发插话不触发（兼容会议中途短暂闲谈）。
环境要求
要求	说明
操作系统	Windows 10 / Windows 11（核心功能依赖 Windows 音频 API）
Python 版本	Python 3.8+（建议 3.10）
麦克风设备	任意 Windows 可识别的麦克风（内置/USB/耳机麦均可）
依赖库
pycaw>=20230407        # Windows 音频设备控制
PyAudio>=0.2.11        # 麦克风音频流采集
vosk>=0.3.45           # 离线中文语音识别（可选，不安装仍可弹窗）
psutil>=5.9.0          # 内存占用监控（可选）
安装与启动
第一步：安装 Python 依赖
pip install -r requirements.txt
如安装 PyAudio 失败，可先安装编译依赖：

pip install pipwin
pipwin install pyaudio
如 vosk 安装失败（Python 3.12+ 常见），详见下方 Q2，不装 vosk 也能正常运行。

第二步：下载 vosk 中文模型（可选但推荐）
vosk 模型用于语音识别（ASR），不安装也可正常使用弹窗提醒功能。

访问 https://alphacephei.com/vosk/models
下载 vosk-model-small-cn-0.22（约 50MB）
解压后将文件夹重命名为 model，放置在项目目录下：
smart-mic-guardian/
├── model/              ← vosk 模型目录（解压后放这里）
│   ├── am/
│   ├── conf/
│   ├── graph/
│   └── ...
├── main.py
└── ...
也可通过环境变量指定模型路径：

set VOSK_MODEL_PATH=D:\your\custom\model\path
第三步：启动程序
python main.py
程序启动后会自动进行 3 秒底噪校准，校准完成后进入 CLI 交互界面。

CLI 命令说明
程序启动后进入交互式命令行，支持以下命令：

命令	说明
s / status	查看当前麦克风状态（开麦/静音/设备信息）
m / mute	静音麦克风
u / unmute	开启麦克风
g / guard	启动/停止 VAD 防误关麦守护（核心功能）
i / intent	查看最近一次 NLP 意图识别结果
r / rules	查看 NLP 关键词规则库（全部意图与关键词）
a / asr	手动触发一次 ASR+NLP 识别（截取近 3 秒音频）
p / predict	查看发言预测状态与关麦屏蔽动态
t / topic	查看话题白名单状态与实时闲聊检测统计（v5.0）
c / calibrate	查看声学特征基线校准状态（v5.0）
h / help	显示帮助信息
q / quit	退出程序
典型使用流程
python main.py
> g           # 启动守护（开始实时监听）
> s           # 随时查看麦克风状态
> p           # 查看发言预测状态
> g           # 再次输入 g 停止守护
> q           # 退出
配置说明
所有参数集中在 config.py，无需修改其他代码，仅调整此文件即可。

音频采集参数
参数	默认值	说明
SAMPLE_RATE	16000	采样率（Hz），vosk 要求 16000
FRAME_DURATION	0.03	单帧时长（秒），即 30ms
FRAME_SIZE	480	单帧采样点数，自动计算
CHANNELS	1	声道数（单声道）
SAMPLE_WIDTH	2	采样位深字节数（16-bit）
VAD 阈值参数
参数	默认值	说明
CALIBRATE_SECS	3.0	底噪校准时长（秒），越长越准确
NOISE_FACTOR	3.0	动态阈值倍率：人声阈值 = 底噪均值 × 3.0
DEFAULT_ENERGY	500.0	校准失败时的默认能量阈值
MIC_OFF_FRAMES	10	连续全零帧数达到此值则判定为物理关麦
调优建议：嘈杂环境可将 NOISE_FACTOR 调高至 4.0~5.0；安静环境可调低至 2.5。

音频缓冲区
参数	默认值	说明
AUDIO_BUFFER_SECS	5.0	VAD 滚动缓冲区时长（需 ≥ ASR_CAPTURE_SECS）
ASR_CAPTURE_SECS	3.0	弹窗触发时截取的音频时长（秒）
PRE_SPEECH_BUFFER_SECS	2.0	发言预测滑动窗口时长（秒）
静默计时与延时等待
参数	默认值	说明
SILENT_THRESHOLD_S	15.0	静音累计达到此值进入延时等待（秒）
DELAY_WAIT_S	15.0	延时等待窗口时长（秒），期间恢复人声则取消提醒
总提醒延迟 = SILENT_THRESHOLD_S + DELAY_WAIT_S = 最快 30 秒后弹窗。

发言预测参数
参数	默认值	说明
PRE_VOICE_LOWER_RATIO	1.05	预发声能量下限（底噪阈值 × 1.05）
PRE_VOICE_UPPER_RATIO	2.5	预发声能量上限（底噪阈值 × 2.5）
SEMANTIC_VALID_SECS	30.0	NLP 铺垫意图有效期（秒）
FUZZY_THRESHOLD	0.65	NLP 模糊匹配最低相似度
ASR 语音验证参数（噪音过滤）
参数	默认值	说明
VOICE_VERIFY_ENABLED	True	是否启用 ASR 语音验证过滤噪音
VOICE_VERIFY_SECS	1.0	验证窗口时长（秒），能量超限后收集音频送入 ASR
MIN_VOICE_TEXT_LEN	1	ASR 返回文本最小有效长度，低于此值视为噪音
闲聊检测参数（v5.0）
参数	默认值	说明
CHITCHAT_ALERT_SECS	3.0	连续闲聊触发弹窗的最低时长（秒）
CHITCHAT_RESET_SECS	5.0	连续非闲聊达到此值后重置闲聊计时（秒）
话题白名单参数（v5.0）
参数	默认值	说明
TOPIC_CALIBRATE_SECS	180	白名单采集时长（秒），默认 3 分钟
TOPIC_MIN_WORD_LEN	2	最小分词长度
TOPIC_MIN_FREQ	2	最低出现频次，>= 此值的词才纳入白名单
TOPIC_TOP_N	50	最多保留前 N 个高频词
TOPIC_MATCH_MIN_LEN	2	匹配时忽略长度低于此值的白名单词
声学基线参数（v5.0）
参数	默认值	说明
ACOUSTIC_CALIBRATE_FRAMES	333	声学基线采集帧数（约 10 秒@30ms/帧）
ACOUSTIC_NEAR_ENERGY_MIN	0.8	「近麦」判定：当前能量 >= 基线均值 × 0.8
ACOUSTIC_NEAR_STABLE_RATIO	0.4	「稳定」判定：变异系数 CV <= 0.4
ACOUSTIC_CHIT_VOLATILE_RATIO	0.6	「波动」判定：CV >= 0.6 时判定为声源偏移
内存与异常参数
参数	默认值	说明
MEMORY_WARN_MB	200.0	内存占用警告阈值（MB）
MEMORY_CLEAR_MB	300.0	内存占用触发清理阈值（MB）
MAX_FRAME_ERRORS	20	连续帧错误上限，超出则重置音频流
DEVICE_RETRY_SECS	5.0	设备异常后重试间隔（秒）
常见问题
Q1：程序提示「找不到麦克风设备」
原因：系统未检测到麦克风，或麦克风被其他程序独占。

解决方案：

打开系统设置 → 隐私 → 麦克风，确认允许桌面应用访问麦克风
检查设备管理器中是否有麦克风设备
关闭可能占用麦克风的其他程序（如 Teams、Zoom）
重新插拔外接麦克风或耳机
Q2：pip install vosk 报错「No matching distribution found」
原因：vosk 尚未发布您当前 Python 版本的预编译 wheel（Python 3.12+ 最常见）。

不影响：不装 vosk 程序也能完全正常运行——弹窗提醒、VAD 守护、静默计时均不受影响，仅 NLP 意图识别和发言预测精度降低。

解决方案（任选一种）：

方案 A：换用国内镜像

pip install vosk -i https://pypi.tuna.tsinghua.edu.cn/simple
方案 B：手动下载 .whl 安装（最可靠）

# 1. 访问 https://github.com/alphacep/vosk-api/releases
# 2. 下载对应您 Python 版本的 .whl（如 vosk-0.3.45-cp310-cp310-win_amd64.whl）
# 3. 在下载目录执行
pip install vosk-0.3.45-cp310-cp310-win_amd64.whl
方案 C：降级 Python 到 3.10/3.11

# 创建 Python 3.10 虚拟环境
py -3.10 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Q3：安装 PyAudio 失败（error: Microsoft Visual C++ 14.0 is required）
解决方案：使用 pipwin 安装预编译版本：

pip install pipwin
pipwin install pyaudio
Q4：ASR 显示「不可用」，弹窗还能正常工作吗？
可以。ASR（语音识别）是可选增强功能，仅影响 NLP 意图识别和发言预测的精准度。
未安装 vosk 模型时，程序仍会正常进行 VAD 检测和 15 秒静默弹窗提醒。

Q5：弹窗触发太频繁/太迟，如何调整？
修改 config.py 中的两个参数：

SILENT_THRESHOLD_S = 15.0  # 调小 → 更快触发；调大 → 需要更长静音才提醒
DELAY_WAIT_S       = 15.0  # 调小 → 缩短缓冲期；调大 → 给更多时间恢复说话
Q6：咳嗽/键盘声/走动会重置计时器，怎么办？
这是 v4.1 已修复的问题。

旧版本 VAD 仅基于「短时能量」判定人声，咳嗽、键盘敲击、走动等噪音能量超过阈值就会被误判为"人声"，导致计时器被错误重置。

v4.1 新增「ASR 语音验证窗口」机制：

VAD 检测到能量超限时，不立即重置计时器
收集 1.0 秒音频送入离线 ASR 快速验证
ASR 识别出有效文本 → 确认是人声 → 重置计时器
ASR 无结果（噪音）→ 静默计时继续累加
验证期间能量回落 → 放弃本次验证
配置参数：

VOICE_VERIFY_ENABLED = True   # 启用 ASR 语音验证（默认开启）
VOICE_VERIFY_SECS = 1.0       # 验证窗口时长（建议 0.8~1.5s）
MIN_VOICE_TEXT_LEN = 1        # 识别文本最小长度
如需回退到纯能量检测（旧逻辑）：

VOICE_VERIFY_ENABLED = False
Q7：底噪较大（风扇/键盘声），VAD 误判为人声
解决方案：调高噪音倍率，让人声阈值高于环境噪音：

NOISE_FACTOR = 4.0   # 默认 3.0，适当调高
或延长底噪校准时间，提升基准精度：

CALIBRATE_SECS = 5.0  # 默认 3.0
Q8：如何运行 72 小时压力测试？
# 模拟模式（无需真实麦克风，适合 CI/离线测试）
python stress_test.py --hours 72 --no-real-mic

# 真实麦克风模式（Windows 环境）
python stress_test.py --hours 72

# 快速验证（30 分钟）
python stress_test.py --hours 0.5 --no-real-mic
测试完成后会自动生成 stress_test_report.txt，如发现问题会追加到 issues.md。

Q9：VOSK_MODEL_PATH 环境变量如何设置？
# Windows CMD
set VOSK_MODEL_PATH=D:\models\vosk-model-small-cn-0.22
python main.py

# Windows PowerShell
$env:VOSK_MODEL_PATH="D:\models\vosk-model-small-cn-0.22"
python main.py
版本日志
v5.2 — 麦克风关闭时彻底休眠，不再弹窗（2026-05-14）
用户反馈：希望在麦克风关闭（静音/关麦）状态下，系统完全不进行任何提醒；只有在麦克风开启时才正常检测和弹窗。

修改内容：

guardian.py：_listen_loop 中 mic_off 状态处理重构：
麦克风关闭时，守护进入休眠模式：不更新发言预测器、不采集声学特征、不触发任何弹窗（包括旧版的「误关麦」弹窗）
麦克风从关闭恢复到开启时，自动重置所有计时器和检测状态（pre_speech.reset() + 闲聊计时器清零）
pre_speech.py：新增 reset() 方法，麦克风恢复时清空能量窗口和语义标记，避免麦克风关闭期间累积的状态在恢复后误触发
移除旧版「误关麦检测」弹窗逻辑（show_mic_off_alert() 不再在 mic_off 时调用），统一为「麦克风开启才提醒」的原则
v5.1 — 修复声学校准器安静环境误报（2026-05-14）
问题来源：用户在安静环境下未说话，声学基线校准却显示「均值=426.8，CV=8.111」，并提示「基线波动较大，建议在安静环境下重新校准」，与用户实际环境矛盾。

根本原因：校准器在校准期间采集了所有帧的能量（包括静音帧），安静环境中偶发的电磁脉冲/USB 电流声导致均值被拉高、CV 极大；同时提示信息未说明校准器采集的是「发言」基线而非「环境安静」基线。

修复内容：

session_calibrator.py：校准期间仅采集 VAD 判定为 voice 的帧，彻底避免静音帧和偶发噪音污染基线。
session_calibrator.py：新增 3σ 异常值过滤，在校准完成前自动去除电磁脉冲等偶发高能量样本。
session_calibrator.py：新增 _set_default_baseline() 默认基线机制；若校准期间未检测到足够有效发言（< 30 帧 voice），自动使用默认基线并在后续检测到 voice 时自动重校准。
session_calibrator.py：提示信息重写，区分「样本不足」「基线稳定」「基线一般」「波动较大」「波动极大」五种场景，避免用户困惑。
guardian.py：feed_frame() 调用增加 is_voice 参数，将 VAD 状态传递给校准器。
v5.0 — 闲聊检测（2026-05-14）
核心重构：从「检测静默 → 弹窗提醒关麦」改为「检测非会议闲聊 → 弹窗提醒关麦」。

新增模块：

session_topic.py — 会议话题白名单管理器：开场 3 分钟自动采集高频专业词，构建本场专属白名单。
session_calibrator.py — 声学特征基线校准器：评估音频变异系数(CV)，区分近距离开麦发言与声源偏移闲聊。
内置 80+ 闲聊词库（饮食/购物/娱乐/亲友/天气交通），覆盖常见生活化场景。
关键变更：

guardian.py — 核心循环重写：VAD 检测到 voice → ASR 语音验证 → 话题分类（MEETING/CHITCHAT/NEUTRAL）→ 闲聊计时/非闲聊重置。
config.py — 新增 12 个参数：闲聊阈值、白名单提取、声学基线三大模块参数。
alert.py — 弹窗支持自定义文案，闲聊提醒可显示命中词。
Web 仪表盘 — 新增 4 个组件（ChitchatDetector、TopicWhitelistPanel、AcousticScorePanel），数据流图更新为 v5.0 流程。
v4.3 — 彻底修复 listen 与发言预测误触发（2026-05-14）
main.py：listen 改用 PyAudio callback 非阻塞模式，解决 stream.read() 阻塞导致无法停止 + 峰值 0 的问题
config.py：发言预测参数全面收紧（LOWER 1.5→2.0、HIT 0.4→0.6、FILL 0.5→0.75）
pre_speech.py：新增 能量上升斜率检查（后半段 ≥ 前半段 × 1.2），静止噪音（风扇/空调）不再误触发
alert.py：弹窗时播放系统提示音（winsound.MessageBeep），防止用户未看到弹窗
v4.2 — Bug 修复与命令说明（2026-05-14）
修复 l/listen 峰值显示（PyAudio 读取麦克风流）
修复发言预测误判（阈值提高至 1.5、窗口填充率检查、冷却期 8s）
补充 CLI 命令详细说明文档
v4.1 — ASR 语音验证（2026-05-14）
ASR 语音验证窗口：能量超限后启动 1s 音频采集 → vosk 识别 → 过滤咳嗽/键盘/走动噪音误触发
发言预测模块：预发声特征 + NLP 语义历史双重预测
Web 仪表盘 10 个可视化组件
v4.0 — 系统架构（2026-05-14）
VAD 人声检测 + 静默计时 + 延时等待弹窗
手动关麦检测（区分误关麦与主动关麦）
离线 ASR + NLP 意图匹配联动
全链路异常捕获与内存监控