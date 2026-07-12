# Changelog

所有版本变动记在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/)。

## [Unreleased]

## [0.3.21] - 2026-07-12

### Added
- **本地 .ts 段缓存**(防阿里云盘风控):
  - `LocalSegmentCache`:磁盘 LRU 缓存,默认上限 200GB,目录 `~/.cache/alist_proxy/ts_segments/`
  - `LocalCacheWorker`:后台 worker 顺序拉取所有段到本地
  - `_proxy_to(cache_to=)`:新增可选参数,转发上游 .ts 时**边发边缓存**(无需重新拉取)
  - **阶段 B 降级 m3u8**:video_preview 失败时,若有 manifest,生成多段 m3u8 — 缓存命中走本地,缺失回退上游并自动缓存
  - **CURRENT_VIDEO 机制**:前端通过 `/__api__/cache/register` 注册正在播放的视频,worker 仅在 CURRENT_VIDEO 匹配时拉取,切走 24 小时没心跳则自动停止
  - 配置:`LOCAL_CACHE_FULL_PREFETCH` (默认 true)、`LOCAL_CACHE_PREFETCH_INTERVAL` (默认 0.15s)

- **缓存进度可视化面板**(`PROXY_FALLBACK_JS._setupCachePanel`):
  - 播放器右下角浮动半透明面板,显示 `▓ N / M 段 (X%)` + 渐变进度条
  - 每 5 秒轮询 `/__api__/cache/stats`,实时反映磁盘累积
  - `MANIFEST_PARSED` 事件触发 register,带真实 segment 数

- **缓存统计 API**:
  - `GET /__api__/cache/stats`:返回总段数/总大小/磁盘用量/最近 10 视频命中率
  - `POST /__api__/cache/register`:前端注册当前播放视频
  - `POST /__api__/debug/workers`:调试用,返回 worker 状态

### Fixed
- **video_preview 失败导致整个视频无法播放**(阿里云盘 500 NotFound.File / 风控):
  - 阶段 B 多段降级 m3u8 替代单段,缓存段从本地读,缺失段自动回退上游拉取
  - `_handle_hls_proxy` 改动:`video_preview` 持续失败时,从 `hls_cache.peek()` 取已过期 URL 兜底尝试

- **`.ts` URL 在 video_preview 失败时无法获取**:
  - `UrlCache.peek()`:返回已存 URL(忽略过期),供 `.ts` handler 兜底旧签名

- **OSS URL `x-oss-process=if_status_eq_404{hls/...}` 含真 `/`,导致段文件名提取错误**:
  - 修复:`url.split("?", 1)[0].rsplit("/", 1)[-1]`(先剥 query 再剥 path)

- **音频变调**(降级 m3u8 段时长估算不准导致 PTS 漂移):
  - manifest 现在保存每段真实 EXTINF 时长
  - `_proxy_m3u8` 解析时记录每段 duration
  - `build_fallback_m3u8` 用真实时长生成 EXTINF,不再硬编码 60s

- **hls.js 默认不激进预加载导致灰条不超前**:
  - `configureHls(h)`:统一配置 `maxBufferLength=14400`(4h)、`maxParallelDownloads=12`(默认 6 翻倍)
  - `maxBufferSize` 突破 60MB 默认上限到 ~1TB

## [0.3.19] - 2026-07-11

### Fixed
- **Edge Tracking Prevention 拦截 jsDelivr CDN 阻止 hls.js 访问 storage**(报错 "Tracking Prevention blocked access to storage for cdn.jsdelivr.net"):
  - 代理新增 `/_static_/hls.min.js` 端点,首启动时从 jsDelivr 拉一次缓存到 `~/.local/share/alist_proxy/hls.min.js`,后续启动直接读本地磁盘,完全脱离外网依赖
  - `PROXY_FALLBACK_JS.loadHlsJs` 加载顺序:本地优先,再尝试 jsDelivr + cdnjs 作 fallback
  - 上 Aliyundrive 上小雅转存视频被清理导致 `video_preview` 返 `object not found` 的情况,这个修复同样无效(视频不在了,任何代理层修复都解决不了)

## [0.3.18] - 2026-07-11

### Fixed
- **视频播到 ~14 分钟 502 黑屏**(阿里云盘签名 URL TTL=15 分钟,代理 `URL_CACHE_TTL=14` 分钟,玩家第 15 分钟 .ts 请求签名过期,Alist 偶尔 `/api/fs/other` 返 5xx,bg 续签跑空):
  - **激进预刷新**:在 `_proxy_m3u8` 成功后检查 `hls_cache.expire_ts(cache_key)` 剩余时间;< 3 分钟就 schedule bg 续签,bg 跑到 14 分钟时缓存已就绪
  - **`HLS_RETRY_DELAYS` 扩大**:`(2, 4, 8)` → `(2, 4, 8, 16, 30)`,共 ~60s 容忍 Alist 偶尔抽风
  - **`_BG_REFRESH_INTERVAL` 加快**:5s → 3s,bg 续签节奏紧凑
  - **所有 502 加 `Retry-After: 3`**:浏览器自动每 3s 重试一次,bg 拿到新 m3u8 后用户无感知

## [0.3.16] - 2026-07-11

### Fixed
- **进度条被拍透明**(0.3.15 仍残留):`.art-progress-loaded`/`.art-progress-played`/`.art-progress-indicator` 的 inline style 有 `background: none transparent !important`,加上文字阴影 cover,导致进度条视觉上消失。修复:
  - 注入 `<style id=__proxy_pbar_css>`,显式给三个子元素设置 `background-color`(半透明白 / 蓝 / 白)
  - `setInterval(2s)` 调 `el.style.removeProperty('background')` 把后写的 inline 清掉,让 CSS 默认色生效

## [0.3.15] - 2026-07-11

### Fixed
- 修复自定义字幕 overlay 把进度条挡住:从 `[class*="art-subtitle"]` 通配改成精确 `.art-subtitle` / `.art-subtitle-line` 等,避免误伤 `.art-subtitle-show` 等含子串的兄弟类

## [0.3.14] - 2026-07-11

### Fixed
- **自定义字幕 overlay 把进度条挡住了**:`position:absolute; bottom: 8%` 正好压在 ArtPlayer 进度条的高度上,而且参与父容器布局让父布局错位。改为:
  - `position: fixed` — 完全脱离布局流,不挤压任何兄弟节点
  - `bottom: 18%` — 抬到进度条上方,够留给"播放/暂停/时长"控件
  - 显式 `background: transparent; box-shadow: none`

## [0.3.13] - 2026-07-11

### Fixed
- **字幕黑底仍是无法通过 CSS 消除**(0.3.11/0.3.12 修复不彻底):诊断显示 ArtPlayer 字幕完全在 Chrome shadow DOM 中,`<track>` cue 是浏览器原生渲染,`::cue {}` / `::cue-region {}` / `::-webkit-media-text-track-container` 都不生效。最终方案:`setupCustomSubtitleRenderer` 完全接管:
  - 设所有字幕轨 `mode='hidden'`(浏览器不渲染原 cue)
  - `requestAnimationFrame` 轮询 `<video>.textTracks[*].activeCues`,读 cue 文本
  - 自己维护一个 `<div id="__proxy_subtitle_overlay__">` 绝对定位覆盖在视频底部中
  - 字幕渲染为内联 `<span>`,transparent bg + 文字阴影描边 + 白字
  - 文字用 `textContent` + 转义防 XSS,换行保留

## [0.3.12] - 2026-07-11

### Fixed
- **MutationObserver 死循环导致页面卡死**(0.3.11 引入):监听 `attributes: true + subtree: true` + `attributeFilter: ['style', 'class']` 时,ArtPlayer 字幕每 ~200ms 改一次 inline style,触发回调里 setProperty 又被 ArtPlayer 覆盖回去,死循环把浏览器卡死。改为只监听 `childList` — 新字幕节点挂载时清一次,后续 inline style 更新由外部 CSS `!important` 接管

## [0.3.11] - 2026-07-11

### Fixed
- **字幕仍有黑底**(诊断:ArtPlayer 用 `.art-subtitle` DOM 渲染,WebVTT STYLE 块只对浏览器原生 `<track>` 生效,inline style 比 CSS 优先级高):
  - CSS 用 `html body [class*="art-subtitle"]` 顶 specificity
  - MutationObserver 监听 `body` subtree:`attributes` filter 包含 `style` / `class`,所有 `.art-subtitle*` 元素属性变化/挂载立刻 `style.setProperty('background','transparent','important')` 改 inline
  - 首次 + 每 1.5s 兜底扫 20 次,覆盖 ArtPlayer 用 rAF 重渲染不触发 DOM mutation 的边界

## [0.3.10] - 2026-07-11

### Added
- **服务端 WebVTT `STYLE` 块注入**(字幕透明背景):`_proxy_subtitle`(转码字幕)和 `_handle_subtitle_file`(.vtt/.ass/.ssa)从上游拿到字幕内容后,在 WEBVTT 头后插入:
  ```
  STYLE
  ::cue { background: transparent !important; color: #fff; text-shadow: 0 0 2px rgba(0,0,0,.95), 0 0 4px rgba(0,0,0,.7); }
  ::cue-region { background-color: transparent !important; }
  ```
  让浏览器原生 `<track>` 渲染时直接透明。WebVTT v3 标准,Chrome/Edge/Firefox 都支持。**纯服务端改,不动上游小雅。**

- 前端 `srtToVtt()` 也补上同样的 STYLE 块,处理外置 `.srt` 转 WebVTT 路径。

### Reverted
- 0.3.8 / 0.3.9 字幕透明背景相关改动(MutationObserver / setProperty important 覆盖,失败且搞乱注入脚本,回滚)

## [0.3.7] - 2026-07-11

### Fixed
- **历史面板加载失败 `path.rsplit is not a function`**:写历史功能时把 Python 的 `rsplit` 误用到了 JS(JS 里是 `split`)。改成 `path.split("/").pop()`

## [0.3.6] - 2026-07-11

### Fixed
- **HLS 视频选中同目录字幕后字幕不显示**:`<track>` 元素能被浏览器枚举到(所以面板能列出),但 hls.js 实际渲染字幕时不读 `<track>`,只读它自己从 m3u8 拿到的字幕轨。点面板时除了 `textTracks.mode='showing'`,现在还调 `art.subtitle.switch({url,type:'vtt',lang,name})`,让 ArtPlayer/hls.js 接管
  - 关闭字幕按钮也调 `art.subtitle.switch({url:'',type:''})` 取消 ArtPlayer 端的字幕轨

## [0.3.5] - 2026-07-11

### Fixed
- **点击视频控制台报"点击的元素没有 data-full"**:上次加虚拟挂载时把 `dataAttrs` 判断写反了,目录反而拿到了 `data-full`,视频/文件拿不到。修复:文件项 `data-full="<path>"`、目录项走 `href="#<path>"` 两条独立路径

## [0.3.4] - 2026-07-11

### Fixed
- **点击视频没反应**(PROXY_FALLBACK_JS 注入到 Alist 页面后整个 IIFE 解析失败)
  - 移除 `loadSiblingSubs` 函数结尾残留的 stray `.catch(e=>{...});` 重复代码块(0.3.3 编辑时遗留)
  - 把 ES2020 可选链 `?.` 改成 ES5 兼容写法,老旧解析器/引擎也能跑
  - JS 用 esprima 4 全量解析通过,确认无其他语法错误

## [0.3.3] - 2026-07-11

### Added
- **同目录独立字幕自动加载**:播放视频时自动扫描同目录的 `.srt/.ass/.ssa/.vtt/.sub` 文件,匹配与视频同名或 `视频名.语言` 前缀的文件(如 `hero.srt`、`hero.zh.srt`、`hero.eng.ass`),自动加载到 `<track>` 并出现在字幕切换面板里
  - 后端 `GET /__api__/sibling_subs?path=<video>` 返回候选字幕列表 `{name, lang, format, url, size}`
  - 后端 `GET /__subtitle_file__/<encoded_path>` 代理下载字幕文件,按扩展名给 Content-Type,加 CORS
  - 前端 `tryInjectSubtitles()` 加载 video_preview API 字幕后,再异步调 `loadSiblingSubs()` 追加同目录字幕
  - SRT 文本在前端用 `srtToVtt()` 转成 WebVTT 再喂给 `<track>`;ASS/SSA/VTT 直通
  - 语言识别支持 ISO-639-1 短码 + 中英文命名(中文、简体、繁体、英文等)
  - 排序:无语言后缀的(默认)排第一,其后按语言字母

## [0.3.2] - 2026-07-11

### Fixed
- **字幕黑色背景遮挡视频**:`injectSubCss` CSS 加强覆盖
  - `video::cue` 同时清掉 `background-color` 和 `background`,加文字阴影描边
  - ArtPlayer 字幕容器 + 所有子元素用 `*` 通配 + `background-image:none` 一并清掉,防某些版本给行级盒子套黑底
  - 仅影响字幕元素,不动 `<video>` 播放控件,不影响视频播放

## [0.3.1] - 2026-07-11

### Added
- **虚拟根目录挂载点**:通过 `EXTRA_ROOT_LINKS="显示名|/path"` 配置,把 Alist 其他 storage 上的路径在代理页根目录以"🔗"虚拟文件夹的形式露出。Alist 端建议把这些 storage 挂到独立子路径(如 `/my_aliyun`),避免和小雅分享库在 Alist 自身根视图撞车。
  - 端点:`GET /__api__/extra_roots`
  - 前端在根目录列表顶部插入虚拟条目 + 一条"小雅分享库"分隔线,点击直接跳到挂载路径
  - 用途:把个人阿里云盘转存目录、小雅分享库之外的存储等统一汇聚到代理页根

## [0.3.0] - 2026-07-11

### Added
- **播放历史功能**(代理页"📜 历史"按钮):
  - 新增 `PlayHistoryStore` 持久化类,存储于 `~/.local/share/alist_proxy/history.json`,上限 200 条
  - 自动记录触发点:`/__stream__/<path>` 首次 Range 命中 + `/__hls__/<path>/media.m3u8` 命中(seek 时的后续 Range 不重复计数)
  - 60 秒内同路径去重,再次播放只 +1 计数并刷新时间
  - 端点:`GET /__api__/history`(列表)、`POST /__api__/history/record`(前端兜底)、`POST /__api__/history/clear`(全清)、`DELETE /__api__/history?path=`(删单条)
  - 前端面板:列表 + 累计播放次数 + "X 分钟前"相对时间 + 单条删除 + 一键清空 + 点击直达播放

## [Unreleased]

### Fixed
- **Windows install.ps1 在中文 Windows 上崩溃**: UTF-8 脚本无 BOM 被 GBK 控制台破坏解析 → 给脚本加 UTF-8 BOM
- **Windows install.ps1 在 PS 5.1 `-File` 模式下 "无法识别 Ensure-Nssm 等函数"**: 5.1 不提升后续定义的函数 → 把 `Ensure-Nssm` / `Register-NssmService` / `Register-SchtasksTask` 三个函数定义挪到主逻辑之前
- **第二次 install 时 `Set-Content : 拒绝访问`**: 首次 install 的 `icacls /grant:r :R` 把 config 收紧到只读,二次 install 无法覆盖 → 改成 `:RW`,并在 Set-Content 前先 icacls /grant 恢复当前用户写权限
- **`schtasks /delete` 任务不存在时退出码非零触发脚本退出**: 用 `cmd /c "schtasks /delete ... >nul 2>&1"` 吞掉错误
- **`ValueError: invalid literal for int() with base 10: '"8080"'`**: `alist_proxy.py` 只读环境变量、不读 config 文件;原 wrapper 直接 `set KEY=VALUE` 把 `LISTEN_PORT="8080"` 的引号带进去 → 改用 PowerShell wrapper 解析 config(去掉外层引号)再 export
- **ScheduledTask 后备模式登录后弹 cmd 窗口**: PowerShell wrapper 跑 `python.exe` 会创建 console,任务计划程序又给 wrapper bat 起一个新 cmd 窗口 → wrapper 改调 `pythonw.exe`(无 console);同时给任务设 `Hidden=true`,任务计划程序登录触发时不再显示 wrapper 窗口

### Added
- **非管理员 Windows 用户的安装路径**: NSSM 服务和 `schtasks /create` 都需要管理员,普通用户(Win10/11 默认账户)装不上 → 增加 PowerShell `Register-ScheduledTask` 后备路径(`LogonType=Interactive` + `RunLevel=Limited`),自动检测并降级
- **`run_proxy.bat` + `run_proxy.ps1` wrapper**: PowerShell 解析 config 设环境变量后再启 python,避免 cmd `for /f` + `^"` 引号剥离的兼容问题

## [0.2.0] - 2026-06-28

### Added
- **HLS URL 续签**: `get_hls_url` 加指数退避重试(2s/4s/8s),容忍 Alist 偶发 `NotFound.File`
- **后台异步续签**: 同步重试用尽后,丢后台线程每 5s 试一次,最长约 30 分钟
- **小雅页面兜底脚本** `PROXY_FALLBACK_JS`: 注入到小雅页 HTML,`MutationObserver` 监听 NotFound 错误文案,触发后切到代理 `/__hls__/` 通道续播
- **字幕基础设施**:
  - `__subtitle__/<base64>` 端点代理阿里云盘 VTT,加 CORS 头
  - m3u8 注入 `EXT-X-MEDIA TYPE=SUBTITLES` 声明,让 hls.js / ArtPlayer 暴露字幕选择
- **小雅页面字幕切换 UI**: 浮动面板 + 透明背景 + 文字阴影,Kodi 风格
- **`/__api__/version`** 端点返回运行时版本
- **`/__health__`** 响应附带版本号
- **Windows 原生安装器** `windows/install.ps1`:
  - 探测 Python 3.11+ (支持 `python` / `py -3` / `python3` / `py -3.11`)
  - 默认下载 NSSM 注册为 Windows Service `xiaoya-proxy`
  - NSSM 不可用时自动降级到 `schtasks onlogon`(无需管理员)
  - `-NonInteractive` / `-NoStart` / `-ScheduledTask` / `-Port` 参数
- **Linux 一键升级** `update.sh`:`git pull` + 文件覆盖 + `systemctl restart`,支持 `--dry-run`

### Fixed
- 搜索结果里点击目录时的页面竞态(`exitSearch` 双触 `route()` 导致页面停在旧路径),`exitSearch(restoreView)` 新增参数,目录点击传 `false` 跳过恢复路由

## [0.1.0] - 2026-06-26

### Added
- 初始发布:Alist 反向代理 + 本地目录索引
- 15 分钟 URL 自动续期
- 简易页面 `__simple__/`(目录浏览 + 全局搜索 + 浏览器内 HLS 播放)
- 一键安装脚本 `install.sh` + `install-remote.sh`