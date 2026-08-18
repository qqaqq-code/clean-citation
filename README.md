# Clean Citaton

Clean Citaton 是一个面向 Codex 的文献引用核验 Skill，也提供可独立运行的命令行程序。项目的最终落脚点是内置 Python 爬取与核验程序。宿主模型负责把自然语言和原始参考文献整理为检索提示，并研究人工核查候选；Python 程序独占正式来源抓取、匹配判定、字段定稿、BibTeX 与 Markdown 生成和只读发布。每条正式字段都对应可回查的第一方证据。

## 研究目的

通用文献工具常通过 Crossref、Semantic Scholar 等聚合平台扩大覆盖面。聚合记录会同时收录预印本、会议版、期刊版和作者资料页，同一研究成果因版本归并而产生年份、卷期、页码、作者顺序和发表状态差异。生成式模型在缺少权威证据时还会补全看似合理的字段，使引用幻觉进入论文写作流程。

Clean Citaton 将目标聚焦为一条干净、可复查、可重复运行的 Python 证据链：

1. 期刊、出版社和会议官方页面或官方 API 提供正式出版记录。
2. OpenReview 提供公开投稿、评审状态与会议收录信息。
3. arXiv 提供最新公开预印本记录。
4. 单一权威记录整体拥有标题、作者顺序、年份、场所、DOI、卷期和页码等核心字段。
5. 宿主模型把用户输入整理为 `citations.json` 检索提示。
6. Python 爬虫访问官方页面与 API，完成规范化、路由、评分和状态分类。
7. Python 导出器依据入选官方记录生成 BibTeX、Markdown、JSON 审计和只读清单。
8. 失败项进入人工候选研究，确认后的 DOI 或官方 URL 回到下一轮 Python 核验。

运行时接入源均为第一方权威源。Crossref 与 Semantic Scholar 作为研究对照保留在项目动机中，正式证据链聚焦官方出版记录、OpenReview 和 arXiv。

```text
自然语言或原始参考文献
  → 宿主模型整理 citations.json 检索提示
  → Python 爬虫按官方源、OpenReview、arXiv 顺序取证
  → Python 匹配器选定单一权威记录
  → Python 导出 references.bib、references.md 与 verification.json
```

## 核心设计

- 官方优先：正式期刊、出版社和会议记录拥有最高优先级。
- Python 定稿：正式引用字段、状态和导出文件全部由确定性 Python 程序生成。
- 模型分工：宿主模型提供检索提示和人工核查候选，正式结果沿官方证据链产生。
- 版本清晰：OpenReview 与 arXiv 分别保留投稿状态和预印本身份。
- 字段同源：一条入选记录提供整组核心字段，形成清晰的版本边界。
- 逐条发布：`progress.json` 在每条完成后更新，适合长列表进度观察。
- 可追溯：`verification.json` 保存候选、分数、来源 URL、访问故障和凭据状态。
- 可复现：缓存、固定路由、固定阈值和运行计划支持同项目复跑。
- 防误改：程序输出采用原子替换、只读属性和 SHA-256 清单。
- 人工闭环：失败条目进入结构化队列，模型提供高可信度第一方候选网页，人工完成最终确认。

## 证据顺序

| 层级 | 角色 | 输出状态 |
|---|---|---|
| L1 | 官方期刊、出版社、会议页面或 API | `FINAL` |
| L1 | 以 OpenReview 作为正式发布平台的会议 | `FINAL` |
| L2 | 其他场景中的 OpenReview 公开记录 | `PROVISIONAL_OPENREVIEW`、`OPENREVIEW_SUBMISSION`、`REJECTED_OPENREVIEW` |
| L3 | arXiv 最新公开记录 | `PREPRINT_ARXIV` |

正式源出现密钥、TLS、HTTP 或网络故障时，程序记录故障并继续执行 OpenReview 与 arXiv。每一层结果均保留自身身份，审计文件同时保留上游访问情况。

## 官方数据源

| Adapter | 第一方来源 | 凭据 |
|---|---|---|
| `neurips` | NeurIPS Proceedings | 公共读取 |
| `pmlr` | Proceedings of Machine Learning Research | 公共读取 |
| `mlsys` | MLSys Proceedings | 公共读取 |
| `acl_anthology` | ACL Anthology | 公共读取 |
| `cvf` | CVF Open Access | 公共读取 |
| `usenix` | USENIX Proceedings | 公共读取 |
| `aaai` | AAAI OJS | 公共读取 |
| `ijcai` | IJCAI Proceedings | 公共读取 |
| `jmlr` | Journal of Machine Learning Research | 公共读取 |
| `vldb` | VLDB Endowment | 公共读取 |
| `openreview` | OpenReview API v2 与 v1 | 公共读取，短期会话令牌选配 |
| `arxiv` | arXiv API | 公共读取 |
| `ieee` | IEEE Xplore Metadata API | `IEEE_XPLORE_API_KEY` |
| `springer` | Springer Nature Meta API v2 | `SPRINGER_NATURE_API_KEY` |
| `elsevier` | Elsevier Article Metadata API | `ELSEVIER_API_KEY` |

AAAI OJS 在部分 Windows Python 环境中会触发 OpenSSL `record layer failure`。运行时先使用 Python 标准网络栈，首次出现连接级 TLS 故障后，仅对 `ojs.aaai.org` 启用操作系统 `curl` TLS 通道。页面、跳转目标和元数据仍来自 AAAI 官方域名，HTTP 状态与访问控制保持原样。

arXiv 适配器遵守官方旧版 API 的访问节奏：全局单连接，相邻请求至少间隔 3.05 秒，并对精确 ID 进行批量查询与缓存。

## 项目结构

```text
.
├─ skills/
│  └─ clean-citaton/
│     ├─ SKILL.md
│     ├─ agents/openai.yaml
│     ├─ references/
│     ├─ scripts/
│     └─ bin/
├─ examples/
├─ .github/workflows/
├─ CONTRIBUTING.md
├─ SECURITY.md
└─ pyproject.toml
```

每个核验任务使用独立项目目录：

```text
citation-projects/<project-name>/
├─ input/
│  └─ citations.json
├─ results/
│  ├─ run-plan.json
│  ├─ progress.json
│  ├─ verification.json
│  ├─ references.bib
│  ├─ references.md
│  ├─ manual-review-queue.json
│  └─ manifest.json
├─ manual-review/
│  ├─ candidates.json
│  └─ candidates.md
└─ .cache/
```

`results/` 与 `.cache/` 由程序管理。`input/` 与 `manual-review/` 承载用户、模型和人工核查信息。同一项目可以直接复跑，Windows 只读文件由发布器安全解锁、原子替换并重新保护。

## 安装到 Codex

### Windows 源码仓库

PowerShell 先进入克隆后的仓库根目录，再执行：

```powershell
$repositoryRoot = (Get-Location).Path
$skillSource = Join-Path $repositoryRoot "skills\clean-citaton"
$codexRoot = if ($env:CODEX_HOME) {
  $env:CODEX_HOME
} else {
  Join-Path $env:USERPROFILE ".codex"
}
$skillsRoot = Join-Path $codexRoot "skills"
$skillLink = Join-Path $skillsRoot "clean-citaton"

if (-not (Test-Path -LiteralPath $skillSource -PathType Container)) {
  throw "Run this command from the Clean Citaton repository root."
}
if (Test-Path -LiteralPath $skillLink) {
  throw "Skill path already exists: $skillLink"
}

New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
New-Item -ItemType Junction -Path $skillLink -Target $skillSource
```

`$skillSource` 会解析为每位用户实际克隆位置下的 `skills\clean-citaton`。仓库内容更新后，Junction 会同步呈现最新版本。

### macOS 与 Linux 源码仓库

终端先进入克隆后的仓库根目录，再执行：

```bash
repository_root="$(pwd)"
codex_root="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$codex_root/skills"
ln -s "$repository_root/skills/clean-citaton" "$codex_root/skills/clean-citaton"
```

GitHub Release 便携包内置独立可执行程序，适合直接分发：

- `clean-citaton-windows-x64.zip`
- `clean-citaton-linux-x64.tar.gz`
- `clean-citaton-macos-arm64.tar.gz`

## 运行

### 便携程序

```powershell
$runtime = ".\skills\clean-citaton\bin\windows-x64\clean-citaton.exe"
& $runtime --project-dir ".\citation-projects\demo" --plan-only
& $runtime --project-dir ".\citation-projects\demo"
```

### Python 源码模式

```powershell
python .\skills\clean-citaton\scripts\clean_citaton.py `
  --project-dir ".\citation-projects\demo" `
  --plan-only

python .\skills\clean-citaton\scripts\clean_citaton.py `
  --project-dir ".\citation-projects\demo"
```

第一阶段由 Python 生成路由与耗时预估。第二阶段由 Python 执行网络抓取和证据匹配，并逐条发布只读结果。公开源缓存会显著缩短同项目复跑时间。

输入格式示例：

```json
{
  "papers": [
    {
      "title": "Attention Is All You Need",
      "authors": ["Ashish Vaswani"],
      "year": 2017,
      "venue": "NeurIPS",
      "arxiv_id": "1706.03762",
      "original_text": "Vaswani et al. Attention Is All You Need. 2017."
    }
  ]
}
```

## 人工核查闭环

程序把本轮缺少可导出记录的条目写入 `results/manual-review-queue.json`。宿主模型读取该队列，访问官方出版社、官方会议、OpenReview 或 arXiv 页面，并把高可信度候选写入 `manual-review/candidates.json` 与 `manual-review/candidates.md`。

每个候选包含：

- 可访问的第一方 URL
- 来源名称与权威角色
- 访问检查时间
- 标题、作者、年份、DOI 或标识符证据
- `HIGH` 置信度标签
- 建议回填的 `official_url` 与 DOI

人工确认后的字段进入 `input/citations.json`，随后由同一确定性流水线重新核验。候选研究与正式 BibTeX 分处两个目录，责任边界清晰。

## 可选凭据

仓库只提供空白变量模板 `.env.example`。个人凭据位于仓库外的用户主目录：

```text
Windows:     %USERPROFILE%\.clean-citaton\credentials.env
macOS/Linux: ~/.clean-citaton/credentials.env
```

内容格式：

```text
IEEE_XPLORE_API_KEY=
SPRINGER_NATURE_API_KEY=
ELSEVIER_API_KEY=
OPENREVIEW_ACCESS_TOKEN=
```

环境变量拥有更高优先级。`--show-config` 仅展示 configured、missing 与 public mode 状态。

```powershell
python .\skills\clean-citaton\scripts\clean_citaton.py --show-config
```

OpenReview 匿名接口触发 `ChallengeRequiredError` 时，官方登录流程可创建最长七天的会话令牌：

```powershell
python .\skills\clean-citaton\scripts\clean_citaton.py --configure-openreview
```

该流程支持 MFA，凭据文件仅保存会话令牌。IEEE、Springer Nature 与 Elsevier 的应用状态和调用额度由各自开发者平台管理。

## 扩展会议与期刊

现有适配器通过用户配置完成场所扩展，Python 运行文件保持发布态。配置文件示例：

```json
{
  "venues": [
    {
      "venue": "Example IEEE Conference",
      "aliases": ["EIC"],
      "official": [
        {
          "adapter": "ieee",
          "role": "official_publication",
          "credential": "IEEE_XPLORE_API_KEY"
        }
      ],
      "fallback": ["openreview", "arxiv"]
    }
  ]
}
```

运行参数：

```powershell
python .\skills\clean-citaton\scripts\clean_citaton.py `
  --project-dir ".\citation-projects\demo" `
  --source-config ".\my-sources.json"
```

全新官方 API 适配器由维护者在开发分支中实现，并配套固定响应样本、速率限制、缓存策略、凭据脱敏和来源角色说明。

## 输出状态

| 状态 | 含义 | BibTeX |
|---|---|---|
| `FINAL` | 正式权威记录 | 导出 |
| `PROVISIONAL_OPENREVIEW` | 非原生场所的已接收 OpenReview 记录 | 带标签导出 |
| `OPENREVIEW_SUBMISSION` | 公开投稿记录 | 以 `@misc` 导出 |
| `REJECTED_OPENREVIEW` | 公开拒稿记录 | 以 `@misc` 和拒稿标签导出 |
| `PREPRINT_ARXIV` | arXiv 最新预印本 | 带标签导出 |
| `SOURCE_UNAVAILABLE` | 上游源出现访问故障，后续层级以空结果结束 | 进入人工核查队列 |
| `UNVERIFIED` | 全部查询正常完成，可靠匹配数量为零 | 进入人工核查队列 |
| `AMBIGUOUS` | 多个候选分数接近 | 进入人工核查队列 |
| `WITHDRAWN_*` | 官方撤稿状态 | 进入审计记录 |

退出码 `0` 表示全部条目达到 `FINAL`，退出码 `2` 表示结果已发布且包含其他状态，退出码 `3` 表示运行文件完整性校验触发。

## 发布到 GitHub

### 1. 创建仓库

在 GitHub 的 `New repository` 页面创建空仓库，仓库名建议使用 `clean-citaton`。README、License 与 Gitignore 初始化项保持为空，本地仓库已经包含这些文件。

### 2. 提交源码

在项目根目录执行：

```powershell
git add .
git status
git commit -m "Initial release: Clean Citaton v1.0.0"
$githubUser = "YOUR_GITHUB_ACCOUNT"
git remote add origin "https://github.com/$githubUser/clean-citaton.git"
git push -u origin main
```

### 3. 创建首个版本

```powershell
git tag -a v1.0.0 -m "Clean Citaton v1.0.0"
git push origin v1.0.0
```

`v1.0.0` 标签会触发 `.github/workflows/build-portable.yml`，构建 Windows、Linux 与 macOS 便携包，并创建对应 GitHub Release。

### 4. 核对发布页

GitHub 的 `Actions` 页面展示三平台构建进度。`Releases` 页面展示版本说明与三个便携包。源码用户可直接克隆仓库，便携程序用户可下载对应平台压缩包。

## 发布完整性

项目使用运行文件哈希、只读发布、缓存脱敏、Skill 结构校验和 GitHub Actions 跨平台构建。维护者发布前执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$validator = Join-Path $codexRoot "skills\.system\skill-creator\scripts\quick_validate.py"
python $validator skills\clean-citaton
python .\skills\clean-citaton\scripts\clean_citaton.py --show-config
```

更多设计细节见 [数据源说明](skills/clean-citaton/references/data-sources.md)、[运行方式](skills/clean-citaton/references/runtime.md)、[输入输出结构](skills/clean-citaton/references/schemas.md) 与 [人工核查流程](skills/clean-citaton/references/manual-review.md)。

## License

MIT License
