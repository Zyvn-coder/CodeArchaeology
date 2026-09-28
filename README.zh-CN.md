# CodeArchaeology

**一台理解代码如何演化的时间机器。**

[English](README.md) | [简体中文](README.zh-CN.md)

> **状态：v0.1，已完成。** 下面四个命令现在就能用。项目还没有发布到 PyPI，
> 所以暂时没有 `pip install` 可用。

## 这个项目要解决什么问题

Git 已经能告诉你代码**改了什么**、**什么时候改的**、**谁改的**。它没法告诉你的是：
代码为什么会长成今天这样。

一个现在有 150 行的函数，不是某一次决定的结果。它来自最初的一版、一次重构、一次加
功能、一个 Bug、一次修复、又一次重构。`git log` 保存了每一个单独事件，却丢掉了这条
线索。

CodeArchaeology 想把这条线索接回来。

它读取本地 Git 仓库，提取事实（提交、diff、文件变化，之后还有 AST 结构），存入
SQLite，并重建代码从第一次提交走到今天的过程。

## 环境要求

- Git，且能在 `PATH` 里找到
- Python 3.11 或更新
- 从源码检出安装时需要 [uv](https://docs.astral.sh/uv/)

## 安装

CodeArchaeology 还没发布到 PyPI。从检出开始：

```console
$ git clone https://github.com/Zyvn-coder/CodeArchaeology
$ cd CodeArchaeology
$ uv sync
$ uv run archaeology --version
archaeology 0.1.0
```

## 用法

所有命令都接受一个"仓库内的目录"作为参数，默认是当前目录。**它们都不会往被分析的
仓库里写任何东西**——分析结果写进你缓存目录里的数据库。

### 分析一个仓库

`analyze` 读取全部历史并存下来。

```console
$ archaeology analyze ~/projects/sample-project
Repository  /home/you/projects/sample-project
Commits     6 (9 file changes)
Range       2024-03-01 to 2024-03-10
HEAD        78c0647a
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

重复运行会替换掉已存的内容，所以加了新提交、rebase 或 amend 之后放心重跑。
由旧版本工具写下的数据库也会以同样方式整表重建。

如果仓库是浅克隆，`analyze` 会在 stderr 上说明。浅克隆里最老的那个提交会被当成
根提交，于是其中每个文件都看起来诞生在那里，合并提交也会报出它从未做过的改动。

### 查看时间线

`timeline` 每个提交打印一行，最新的在最前。消息列用终端剩下的全部宽度。

```console
$ archaeology timeline ~/projects/sample-project
Repository  /home/you/projects/sample-project
Commits     6 (9 file changes)
Range       2024-03-01 to 2024-03-10
HEAD        78c0647a

 SHA        DATE         AUTHOR         FILES          +/-   MESSAGE
 ──────────────────────────────────────────────────────────────────────────────────────────
 78c0647a   2024-03-10   Ada Lovelace       3        +5/-5   Add logo and unicode module, drop legacy helper
 bbed4c45   2024-03-08   Ada Lovelace       0        +0/-0   Merge branch 'feature/caching'
 8eaff71d   2024-03-06   Ada Lovelace       1        +6/-0   Fix login bug
 63d9a636   2024-03-05   Ada Lovelace       1       +12/-0   Add caching
 ed9c175d   2024-03-03   Ada Lovelace       1        +0/-0   Move app module into the core package
 392cc0db   2024-03-01   Ada Lovelace       3       +21/-0   Initial commit
```

`--limit N` 少显示几条，并告诉你还剩多少条没显示。`--all` 显示全部。

```console
$ archaeology timeline --limit 2
...
4 more commits. Use --all to see them.
```

`--json` 输出同样的数据，供别的程序读取。注意其中的 `sha` 是完整的 40 位，
和表格里缩写的不同。

```console
$ archaeology timeline --json --limit 1
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "commits": [
    {
      "sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
      "date": "2024-03-10",
      "author": "Ada Lovelace",
      "files_changed": 3,
      "insertions": 5,
      "deletions": 5,
      "message": "Add logo and unicode module, drop legacy helper"
    }
  ]
}
```

### 当分析结果已经过期

`analyze` 取的是一份快照。如果之后仓库又有了新提交，这份快照本身没错，只是不再
完整，所以 `timeline` 会告诉你。提示走 stderr，因此 `--json` 的 stdout 依然可以
被程序解析：

```console
$ archaeology timeline --limit 2
Note: this analysis stops at 78c0647a, but HEAD is now a80420c9; run 'archaeology analyze /home/you/projects/sample-project' to refresh it
Repository  /home/you/projects/sample-project
Commits     6 (9 file changes)
Range       2024-03-01 to 2024-03-10
HEAD        78c0647a

 SHA        DATE         AUTHOR         FILES          +/-   MESSAGE
 ──────────────────────────────────────────────────────────────────────────────────────────
 78c0647a   2024-03-10   Ada Lovelace       3        +5/-5   Add logo and unicode module, drop legacy helper
 bbed4c45   2024-03-08   Ada Lovelace       0        +0/-0   Merge branch 'feature/caching'

4 more commits. Use --all to see them.
```

退出码仍然是 0：存下来的历史读得出来，只是落后了。

### 查看改动最频繁的文件

`hotspots` 按「动过这个文件的提交数」排名。文件按**身份**计数而不是按名字，所以一个
改过名的文件只会出现一次，并带着它完整的历史，而不是每换一个名字就多一行。

```console
$ archaeology hotspots ~/projects/sample-project
Most Active Files

1. core/app.py
   3 commits
   +19 / -0

2. README.md
   1 commit
   +3 / -0

3. assets/logo.png
   1 commit
   +0 / -0

4. core/cache.py
   1 commit
   +12 / -0

5. 工具/文本.py
   1 commit
   +5 / -0

Frequent change is not importance: the reason each of these files is busy is not something this tool can see.
```

`--limit N` 少显示几个，并告诉你还剩多少。`--all` 显示全部。已被删除的文件不在榜单
里 —— 热点是一个「地方」，而已经不存在的文件不再是地方。

**热点不是判决。** 同一个数字可能来自核心代码、来自不断出问题的代码、来自反复变动
的需求、来自正在进行中的重构，也可能只是这个文件被编辑得比较勤。CodeArchaeology
分不清这些，所以它不去分：它只报告一个文件被改了多少次，到此为止。

### 查看单个提交

`commit` 完整展示一个提交。sha 给前缀就够，和 git 的习惯一致。

```console
$ archaeology commit ed9c175d ~/projects/sample-project
Commit      ed9c175d27ed94fcfc21cebb09b09a5e093e0aa6
Author      Ada Lovelace <ada@example.com>
Authored    2024-03-03 09:00:00 +0000
Committed   2024-03-03 09:00:00 +0000
Parents     392cc0db

Move app module into the core package

 FILE                                                    +/-   CHANGE
 ──────────────────────────────────────────────────────────────────────────
 app.py → core/app.py                                  +0/-0   renamed
```

消息无论多长都会完整打印。`Authored` 和 `Committed` 在被 rebase 过的提交上会不同，
并且两者都保留提交者所在机器当时的时区偏移量。

合并提交会显示两个父节点、没有任何文件，因为 `git log` 对合并不输出 diff：

```console
$ archaeology commit bbed4c45 ~/projects/sample-project
Commit      bbed4c456457f115e0687de6d04580cc8276f20e
Author      Ada Lovelace <ada@example.com>
Authored    2024-03-08 09:00:00 +0000
Committed   2024-03-08 09:00:00 +0000
Parents     8eaff71d 63d9a636

Merge branch 'feature/caching'

No file changes recorded (git prints no diff for a merge commit).
```

### 数据库放在哪

数据库文件名是仓库绝对路径的摘要，所以同一个项目的两份检出各有一份库。

| 平台 | 位置 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\codearchaeology\` |
| macOS | `~/Library/Caches/codearchaeology/` |
| Linux 及其他 | `$XDG_CACHE_HOME/codearchaeology/`，没有该变量时用 `~/.cache/codearchaeology/` |

设置环境变量 `CODEARCHAEOLOGY_CACHE_DIR` 可以换地方，或者给任意命令加 `--db` 直接
指定文件。

## 项目结构

```
src/codearchaeology/
    cli.py          Typer 应用与四个命令
    analysis.py     一次分析：读仓库、写数据库
    cache.py        分析数据库放在哪
    history.py      调用 git 并把它的输出解析成 Commit 对象
    storage.py      SQLite 表结构与查询
    timeline.py     时间线视图：行数据、表格、JSON
    commit.py       单个提交的视图
    lifecycle.py    从存下来的历史里重建每个文件的一辈子
    statistics.py   汇总一个文件一辈子的那些数字
    hotspots.py     按改动频繁程度给文件排名
    formatting.py   两个视图共用的小工具
tests/
    sample_repo.py  构造一个确定性的小仓库供测试使用
    conftest.py     把这个仓库交给每个测试的 fixture
```

## 核心原则

以下六条不是口号，它们约束代码被允许怎么写。

1. **Core First** —— 核心不能依赖 LLM。任何功能都必须能完全不用 AI 跑通。
   AI 只是上面的一层，永远不是地基。
2. **Local First** —— 任何 Git 仓库都必须能在本地分析，不依赖远程服务。
3. **Evidence First** —— 任何结论都必须能追溯到具体的 commit / diff / AST 节点。
   禁止凭空推测。
4. **Python-only v0.x** —— v0.3 之前 AST 分析只支持 Python。
   多语言支持在路线图上，不在当前范围内。
5. **先 CLI，后 Web** —— v0.3 之前不做 Web UI。
6. **SQLite 是唯一存储** —— v0.x 不引入 PostgreSQL / Redis / 向量数据库。

## 路线图

| 阶段 | 增加什么 | 状态 |
|---|---|---|
| v0.1 | Git 扫描、提交历史、文件改动、SQLite 存储、CLI 时间线 | 已完成 |
| v0.2 | 文件生命周期、代码热点 | 开发中 |
| v0.3 | AST 分析、函数与类的演化 | 计划中 |
| v0.4 | 基于证据层的 AI 解释（Provider 可替换） | 计划中 |
| v0.5 | Developer Memory —— 你自己的技术使用轨迹 | 计划中 |
| v0.6 | AI 参与的改动分析与回放 | 计划中 |

每个阶段一次只做一个，前一个稳定之前不设计后一个。

## 已知限制

- `analyze` 每次都重扫全部历史，没有增量更新。
- `--limit` 只影响打印出来的内容：整个历史会先从数据库读出来，然后再切片。
- 合并提交会记下父节点但没有文件变化，因为 `git log` 对合并不输出 diff。
- 二进制文件不记录增删行数，显示为 `-`。
- 不读取也不保存 patch 内容，所以工具从不显示 diff 正文，只显示改动文件和行数。
- 重命名检测用的是 git 的默认阈值（相似度 50%）。改名时如果内容改掉大半，git 会
  报成「删除 + 新增」，这个文件的一辈子就被劈成两段。git 给出的相似度分数会随重命名
  一起存下来，所以一次重命名离阈值有多近，事后仍然查得到。这一点会波及统计数字：
  一个文件被记上多少添加和删除，取决于 git 的那个判定；净变化则不受影响。重命名
  没被识别的文件还会被打散成好几行，于是它可能从一个本该属于它的榜单上掉下去。
- 浅克隆不是「短一点的历史」，而是形状不同的历史。git 会把它手里最老的那个提交当成
  根提交，于是其中每个文件都看起来诞生在那里，合并提交也会报出它从未做过的改动。
  `analyze` 会在 stderr 上就此给出提示。
- `commit` 只接受 sha 和 sha 前缀，不接受 `HEAD`、分支名这类引用。
- 数据库以仓库绝对路径为键，所以移动或重命名仓库后需要重新分析。
- 由 CI 在 Linux 与 Windows、Python 3.11 与 3.13 下验证；macOS 尚未验证。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
