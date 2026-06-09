智能防误关麦助手 Bug 与优化跟踪
格式说明：- [x] 已修复 ｜ - [ ] 待处理 ｜优先级 🔴高 🟡中 🟢低

Phase 1 — VAD + 弹窗基础版
[x] 🔴 弹窗重复创建 BUG（已修复 v3.0）

现象：连续触发提醒时，每次都创建新 tk.Tk() 实例并启动新 mainloop 线程， 导致多个弹窗叠加、GUI 进程资源堆积。
根因：AlertWindow._run_window() 每次调用均 tk.Tk() + mainloop()， _instance 仅为占位对象，实际无法复用。
修复：重构 alert.py 为进程级单例模式，__init__ 时创建唯一 tk.Tk 实例 并在守护线程中启动 mainloop；后续调用通过 after(0, ...) 调度 deiconify() 显示窗口；关闭时调用 withdraw() 隐藏而非 destroy()。
验证：多次快速触发提醒，确认 _instance 全程为同一对象地址。
[x] 🔴 音频缓冲区无上限内存泄漏（已修复 v1.0）

现象：vad.py 早期版本中 _audio_buffer 为普通 list，长时间运行持续追加 导致内存线性增长。
修复：改用 collections.deque(maxlen=...) 滑动窗口，自动丢弃过期帧。
验证：72h 压测内存占用保持平稳。
[x] 🟡 底噪校准期间噪音过大默认阈值失效（已修复 v1.0）

现象：环境噪音能量大于 DEFAULT_ENERGY(500) 时，所有帧均判定为人声。
修复：calibrate() 校准结果若仍低于 DEFAULT_ENERGY 则抬高阈值； NOISE_FACTOR 设为 3.0 提供足够边距。
Phase 2 — ASR + NLP 集成
[x] 🔴 ASR 解析空音频导致 vosk 崩溃（已修复 v2.0）

现象：麦克风刚启动缓冲区不足时，recognize_pcm(b"") 引发 vosk 内部断言错误。
修复：_run_asr_nlp_pipeline() 在截取 PCM 后判断 if not pcm_data 则跳过 ASR。
验证：模拟缓冲区为空场景，确认跳过而非崩溃。
[x] 🟡 NLP 模糊匹配阈值过低，无关文本误命中（已修复 v2.0）

现象：difflib.SequenceMatcher 默认相似度 0.6，导致短词如「说」命中「我说一下」。
修复：FUZZY_THRESHOLD 统一提升至 0.65，并写入 config.py 集中管理。
[x] 🟡 _reset_recognizer 每次重新加载模型，耗时过长（已修复 v2.0）

现象：每次 ASR 识别后重建 KaldiRecognizer，加载模型耗时约 200ms， 堆积后影响实时响应。
修复：使用同一 model 实例重建 KaldiRecognizer，不重新调用 Model()。
Phase 3 — 发言预测 + 动态关麦屏蔽
[x] 🟡 预发声能量窗口未满时误判（已修复 v3.0）

现象：程序刚启动时滑动窗口帧数不足 WINDOW_SECS，均值被 0 帧拉低， 导致预发声条件提前命中。
修复：update_audio() 在 len(_energy_window) < max_frames // 2 时 跳过预发声判定。
[x] 🟡 语义历史过期未及时失效（已修复 v3.0）

现象：_semantic_marked_at 超过 SEMANTIC_VALID_SECS(30s) 后， 如未调用 tick()，is_semantic_ready 仍返回 True。
修复：每次读取 is_semantic_ready property 时调用 _refresh_semantic_ready() 进行懒刷新；tick() 亦主动刷新。
Phase 4 — 系统联调 + 异常捕获 + 压力测试
[x] 🔴 各模块音频参数硬编码不一致（已修复 v4.0）

现象：vad.py SAMPLE_RATE=16000，asr.py 默认 sample_rate=16000， pre_speech.py 内 WINDOW_SECS=2.0，各处独立维护，一旦某处修改 其余模块不跟进将导致数据错位。
修复：新增 config.py 统一管理所有全局参数，各模块改为 from config import ...。 config.validate() 启动时自动校验参数一致性。
[x] 🔴 全局状态变量无线程保护，并发读写竞态（已修复 v4.0）

现象：guardian.py 中 silent_time / wait_flag / delay_cnt 在监听线程写入， CLI 命令（s / status、p / predict）在主线程读取，存在竞态。
修复：__init__ 添加 self._state_lock = threading.Lock()； _reset_cycle() 与 start() 重置块均通过 with self._state_lock 保护。
[x] 🟡 硬件异常导致监听线程静默退出（已修复 v4.0）

现象：麦克风被拔出或被系统占用时，stream.read() 抛出 OSError， 原代码 break 直接退出循环且无任何提示。
修复：捕获 OSError 调用 handle_hardware_error()，连续错误达 MAX_FRAME_ERRORS(20) 次则调用 _restart_vad_stream() 重置音频流； 单次错误仅跳过当前帧，不退出循环。
[x] 🟡 文件读写异常（intent_rules.json 缺失）导致程序崩溃（已修复 v4.0）

现象：intent_rules.json 丢失或格式损坏时，nlp.py 抛出 FileNotFoundError / json.JSONDecodeError 未捕获，导致程序启动崩溃。
修复：IntentMatcher.__init__ 捕获文件异常，回退到 _default_rules() 内置默认规则；error_handler.handle_file_io_error() 统一记录日志。
[x] 🟢 psutil 未安装时内存监控静默失败（已修复 v4.0）

现象：check_memory_usage() 在 psutil 未安装时调用报 NameError。
修复：error_handler.py 顶部做可选导入 _PSUTIL_AVAILABLE， 不可用时 check_memory_usage() 直接返回 -1.0 并打印安装提示。
[x] 🟢 stress_test.py 在非 Windows 平台因 platform check 无法运行（已修复 v4.0）

现象：guardian.py 顶层 sys.platform != "win32" 调用 sys.exit(1)， 导致 stress_test.py 在 Linux/macOS 的 CI 环境无法运行。
修复：stress_test.py 模拟模式（--no-real-mic）通过 __new__ 绕过 MicGuardian 构造器的平台检查，直接替换 _vad 为 SimulatedVAD。