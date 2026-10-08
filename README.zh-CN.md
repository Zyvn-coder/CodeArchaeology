# CodeArchaeology

**一台理解代码如何演化的时间机器。**

[English](README.md) | [简体中文](README.zh-CN.md)

> **状态：v0.5.1 已发布——注释 tag `v0.5.1`，附 GitHub Release，四个 CI job 在该提交
> 上全部通过之后才打的。** 现在有十七个
> 命令可用：v0.4 的十个（到 `explain` 为止，它是唯一会和模型说话的一个），加上 memory
> 的七个动作（`create`、`list`、`show`、`supersede`、`invalidate`、`adopt`、`export`）
> ——它们把人知道的事情存在证据旁边，并且永远不把这些事情当成证据。**证据行可以靠重读 git
> 重建，memory 行不能**——这是 v0.5 对数据库唯一的一处改动，写在下面的"数据库放在哪"一节里。
> v0.5.1 加的是 memory 可携带性的一半——memory 可以写成一个由你放置的文件——格式已经冻结，
> 好让 import 能在它之上设计，写在
> [`docs/v0.5.1-portability-design.md`](docs/v0.5.1-portability-design.md)；主张要做这件事的
> 审计是 [`docs/v0.5-product-audit.md`](docs/v0.5-product-audit.md)。v0.3.x 的证据层已冻结，
> 它是什么、不是什么写在
> [`docs/v0.3-final-state.md`](docs/v0.3-final-state.md) 里；v0.4 加了什么、由什么守住，
> 写在 [`docs/v0.4-final-state.md`](docs/v0.4-final-state.md) 里；v0.5 是什么、由什么守住，
> 写在 [`docs/v0.5-final-state.md`](docs/v0.5-final-state.md) 里，memory 这一层的定义在
> [`docs/v0.5-design.md`](docs/v0.5-design.md)，数据模型在
> [`docs/v0.5-storage-design.md`](docs/v0.5-storage-design.md)，命令行在
> [`docs/v0.5-cli-design.md`](docs/v0.5-cli-design.md)。**模型的答案是什么、不是什么**
> ——是对证据的解读，绝不是"当时发生了什么"的记录——固定在
> [`docs/v0.4-problem-definition.md`](docs/v0.4-problem-definition.md) §12。项目还没有
> 发布到 PyPI，所以暂时没有 `pip install` 可用。

## 这个项目要解决什么问题

Git 已经能告诉你代码**改了什么**、**什么时候改的**、**谁改的**。它没法告诉你的是：
代码为什么会长成今天这样。

一个现在有 150 行的函数，不是某一次决定的结果。它来自最初的一版、一次重构、一次加
功能、一个 Bug、一次修复、又一次重构。`git log` 保存了每一个单独事件，却丢掉了这条
线索。

CodeArchaeology 想把这条线索接回来。

它读取本地 Git 仓库，提取事实（提交、diff、文件变化，以及每一个 Python 文件版本的
结构），存入 SQLite，并重建代码从第一次提交走到今天的过程。

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
archaeology 0.5.1
```

## 用法

所有命令都接受一个"仓库内的目录"作为参数，默认是当前目录。**它们都不会往被分析的
仓库里写任何东西**——分析结果写进你缓存目录里的数据库。

读侧的命令——`timeline`、`hotspots`、`files`、`file`、`structure`、`cochange`、
`explain`、`memory list`、`memory show`——支持 `--json`，用同样的字段名把同样的事实输出
给别的程序读。stdout 上只有 JSON——「分析结果已过期」的提示走 stderr——所以无论快照是不是
最新的，输出都能被解析。`commit` 目前还没有 JSON 形式；写入方 `analyze`、`ast` 和
memory 的各个写入动作打印的是这次做了什么。

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

重复运行会刷新已存的内容，所以加了新提交、rebase 或 amend 之后放心重跑：git 已经没有的
提交会带着它的行一起消失，还成立的东西都留着。唯一的例外是由旧版本工具写下的数据库——
那种情况会把证据整表重建，结构层也在内，之后要再跑一次 `ast`——而 memory 会活过这次重建，
因为它是这个文件里唯一一样 git 给不回来的东西。

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
 ───────────────────────────────────────────────────────────────────────────────────────────────────────────
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
Note: this analysis stops at 78c0647a, but HEAD is now fbc2da34; run 'archaeology analyze /home/you/projects/sample-project' to refresh it
Repository  /home/you/projects/sample-project
Commits     6 (9 file changes)
Range       2024-03-01 to 2024-03-10
HEAD        78c0647a

 SHA        DATE         AUTHOR         FILES          +/-   MESSAGE
 ───────────────────────────────────────────────────────────────────────────────────────────────────────────
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

`--json` 把这份排名输出给别的程序读。排名的形状不是清单的形状：排名里不可能有已删除
的文件，所以它没有 `state` 字段，行的顺序按计数。

```console
$ archaeology hotspots --json --limit 2 ~/projects/sample-project
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "files": [
    {
      "path": "core/app.py",
      "commits": 3,
      "additions": 19,
      "deletions": 0,
      "path_history": [
        "app.py",
        "core/app.py"
      ]
    },
    {
      "path": "README.md",
      "commits": 1,
      "additions": 3,
      "deletions": 0,
      "path_history": [
        "README.md"
      ]
    }
  ]
}
```

**热点不是判决。** 同一个数字可能来自核心代码、来自不断出问题的代码、来自反复变动
的需求、来自正在进行中的重构，也可能只是这个文件被编辑得比较勤。CodeArchaeology
分不清这些，所以它不去分：它只报告一个文件被改了多少次，到此为止。

### 列出所有文件

`files` 回答的是另一个问题：不是「哪些文件最忙」，而是「这个仓库里有什么」。它列出
历史中出现过的每一个文件 —— 包括已删除的 —— 一行一个，按路径排序，就像目录列表。

```console
$ archaeology files --all ~/projects/sample-project
 FILE                   STATE     COMMITS   +LINES   -LINES
 ──────────────────────────────────────────────────────────
 README.md              alive           1        3        0
 assets/logo.png        alive           1        0        0
 core/app.py            alive           3       19        0
 core/cache.py          alive           1       12        0
 legacy.py              deleted         2        5        5
 工具/文本.py           alive           1        5        0

Deleted files are in the list on purpose: this is what the history contains, not what the working tree contains.
```

`STATE` 这一列决定了这一行的含义：`legacy.py` 已被删除，所以它的数字描述的是一段
已经结束的生命，命令把这一点直接写出来。改过名的文件仍然只有一行 —— `core/app.py`
就是出生时叫 `app.py` 的那个文件 —— 因为「这里有几个文件」对每个文件只能有一个答案。

`--limit N`（默认 20）和 `--all` 的行为与时间线一致。`hotspots` 按计数排名活着的
文件，这个命令按路径列出全部；两者对同一个文件打印的数字完全一致。

`--json` 输出**整份清单**，不做切片：`--limit` 是给终端看的便利，而一个程序既然要
清单就是要全部。每一行都带 `state`，也带上它计数时覆盖的那些名字 —— 一行是按**身份**
计数的，没有这条改名链，就没法区分「一个改过名的文件」和「两个不相干的文件」。

```console
$ archaeology files --json ~/projects/sample-project
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "files": [
    {
      "path": "README.md",
      "state": "alive",
      "commits": 1,
      "additions": 3,
      "deletions": 0,
      "path_history": [
        "README.md"
      ]
    },
    {
      "path": "assets/logo.png",
      "state": "alive",
      "commits": 1,
      "additions": 0,
      "deletions": 0,
      "path_history": [
        "assets/logo.png"
      ]
    },
    {
      "path": "core/app.py",
      "state": "alive",
      "commits": 3,
      "additions": 19,
      "deletions": 0,
      "path_history": [
        "app.py",
        "core/app.py"
      ]
    },
    {
      "path": "core/cache.py",
      "state": "alive",
      "commits": 1,
      "additions": 12,
      "deletions": 0,
      "path_history": [
        "core/cache.py"
      ]
    },
    {
      "path": "legacy.py",
      "state": "deleted",
      "commits": 2,
      "additions": 5,
      "deletions": 5,
      "path_history": [
        "legacy.py"
      ]
    },
    {
      "path": "工具/文本.py",
      "state": "alive",
      "commits": 1,
      "additions": 5,
      "deletions": 0,
      "path_history": [
        "工具/文本.py"
      ]
    }
  ]
}
```

关于已删除文件的那句话是说给人听的；`state` 对程序说的是同一件事，所以 JSON 里不
重复它。

### 查看单个文件

`file` 展示一个文件的完整一生：它用过的名字、何时出现、最后何时被改、以及它消耗了
多少行。

```console
$ archaeology file core/app.py ~/projects/sample-project
core/app.py
History:        app.py -> core/app.py

Created:        2024-03-01 09:00:00 +0000  392cc0db
Last modified:  2024-03-06 09:00:00 +0000  8eaff71d

Commits:        3
Modifications:  1
Renames:        1
Additions:      19
Deletions:      0

Net change:     +19
```

文件可以用它**任何一个曾用名**找到，所以 `file app.py` 和 `file core/app.py` 指向
同一个文件。如果一个名字曾经属于多个文件（被删除后重建，或者改名离开后名字又被收回），
它会**各打印一块**，而不是替你挑一个。

对创建之后从未被修改过的文件，`Last modified` 显示 `-`：没有任何东西修改过它，
所以没有这样一个时间可报。

`--json` 输出的是同一批事实。它永远是一个「装着列表的对象」，哪怕这个名字只属于一个
文件——形状不随历史变化：

```console
$ archaeology file core/app.py --json ~/projects/sample-project
{
  "path": "core/app.py",
  "files": [
    {
      "path": "core/app.py",
      "commits": 3,
      "additions": 19,
      "deletions": 0,
      "renames": 1,
      "deleted": false,
      "created_at": "2024-03-01T09:00:00+00:00",
      "created_sha": "392cc0db21a7a299e0457555a38c1cfeef7578a3",
      "last_modified_at": "2024-03-06T09:00:00+00:00",
      "last_modified_sha": "8eaff71d34a6f7eb6e27366ca8f72ab22cbba204",
      "deleted_at": null,
      "deleted_sha": null,
      "modifications": 1,
      "binary_changes": 0,
      "path_history": [
        "app.py",
        "core/app.py"
      ],
      "net_change": 19
    }
  ]
}
```

最外层的 `path` 是**你问的那个名字**，每一项里的 `path` 是那个文件**现在叫什么**。
两者不同，正好说明这个文件改过名。文件从来没有过的时间是 `null`，不用别的值顶替——
创建后从未被编辑的文件没有 `last_modified_at`，还活着的文件没有 `deleted_at`。时间戳
保留提交者所在机器当时的时区偏移量，因为一个提交的时间离开了偏移量就没有意义。

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

### 读取 Python 结构

`ast` 读取已存历史里的每一个 Python 文件版本，记录其中有什么：类、函数和方法，以及
它们的行号范围、装饰器和限定名。这是工具里昂贵的那一半——实测十万个提交，读取加解析
要以分钟计，而 `analyze` 只要几秒——所以它是一条独立的命令，而不是 `analyze` 的一部分。

```console
$ archaeology ast ~/projects/sample-project
Repository   /home/you/projects/sample-project
Versions     6 file versions, 13 definitions
Parsed       6 parsed, 0 reused, 0 could not be parsed
Database     /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

`Parsed` 这一行说的是**这一次**做了什么：已经读过的版本会被复用而不是重新解析，解析
不了的版本计入第三个数，而不是被悄悄丢掉。只读 Python 文件，历史里的其他文件不参与。

### 结构层的五条规则

下面两条命令打印的一切，都由这五条规则决定，所以值得先读规则再看输出——输出正是被
它们塑造的。

1. **快照就是事实本身。** `definition_versions` 保存的是每个**读得出来**的文件版本里
   实际存在过的 definitions。它是快照，不是变更日志：没有变化的 definition 会在下一个
   版本里再存一次，`unchanged` 是关于那个版本的事实，而不是「没有消息」。
2. **删除是推导出来的，从不存储。** `change_type` 只允许是 `created`、`modified` 或
   `unchanged`。已经不存在的 definition 根本没有行；删除由查询层从它所在的那份快照与
   下一份读出来的快照推导得到。
3. **解析失败是不确定性，不是删除。** 读不出来的版本会带着解析器自己的原话记为
   unreadable，并且不产生任何 definition。工具不会从这个「缺席」推出任何结论：文件
   读不出来的 definition 不会被报成 deleted，命令会明说这个文件读不出来，而不是打印
   一个「0 个 definition」的列表。
4. **`change_type` 是缓存的比较结果，不是语义变更日志。** 这三个词说的是一个
   definition 与**上一份读得出来的快照**的关系。`modified` 意味着它的结构变了，不意味
   着它的含义变了；`unchanged` 可以跨越一个读不出来的版本，所以每一行都会把「和哪个
   版本比的」写在旁边。
5. **`analyze` 与 `ast` 是两趟独立的处理。** `analyze` 写 Git 事实，保持廉价；`ast` 写
   Python 结构，是昂贵的那一趟。两者都不碰对方的表。

### 查看一个文件的结构

```console
$ archaeology structure core/app.py ~/projects/sample-project
core/app.py
History:     app.py -> core/app.py
Version:     2024-03-06 09:00:00 +0000  8eaff71d
Definitions: 3 (3 functions)

 KIND             DEFINITION             LINES   DECORATORS           CHANGE
 ──────────────────────────────────────────────────────────────────────────────
 function         login                    4-7                        modified
 function         logout                 10-11                        created
 function         main                   14-15                        unchanged
```

显示的版本是这个文件最新存下来的那一个；`--commit <前缀>` 可以看它在任何一个改动过它的
提交上的样子；改过名的文件会以现在的名字显示，并在上面列出它用过的名字。

`LINES` 是那个 definition 在那个版本里的位置。`CHANGE` 就是上面第 4 条说的比较：
`main` 从第 8 行挪到了第 14 行，仍然是 `unchanged`，因为它本身没变，变的只是它待的
地方。方法就是「外层作用域是同一文件里的类」的函数，限定名本身已经说明了这一点：

```console
$ archaeology structure core/cache.py ~/projects/sample-project
core/cache.py
Version:     2024-03-05 09:00:00 +0000  63d9a636
Definitions: 4 (1 class, 3 functions, 3 of them methods)

 KIND             DEFINITION             LINES   DECORATORS           CHANGE
 ──────────────────────────────────────────────────────────────────────────────
 class            Cache                   4-12                        created
 function         Cache.__init__           5-6                        created
 function         Cache.get                8-9                        created
 function         Cache.set              11-12                        created
```

`--json` 把同一个版本输出给别的程序读。版本的状态是明写出来的，不靠「列表是空的」去
猜；每一行的 change type 是跟哪个版本比的，也写在里面：

```console
$ archaeology structure core/app.py --json ~/projects/sample-project
{
  "path": "core/app.py",
  "path_history": [
    "app.py",
    "core/app.py"
  ],
  "commit_sha": "8eaff71d34a6f7eb6e27366ca8f72ab22cbba204",
  "committed_at": "2024-03-06T09:00:00+00:00",
  "state": "read",
  "reason": null,
  "parse_error": null,
  "error_lineno": null,
  "error_offset": null,
  "compared_with": "ed9c175d27ed94fcfc21cebb09b09a5e093e0aa6",
  "blind_spots": [],
  "definitions": [
    {
      "qualname": "login",
      "kind": "function",
      "lineno": 4,
      "end_lineno": 7,
      "decorators": [],
      "change_type": "modified"
    },
    {
      "qualname": "logout",
      "kind": "function",
      "lineno": 10,
      "end_lineno": 11,
      "decorators": [],
      "change_type": "created"
    },
    {
      "qualname": "main",
      "kind": "function",
      "lineno": 14,
      "end_lineno": 15,
      "decorators": [],
      "change_type": "unchanged"
    }
  ]
}
```

### 查看 definitions 的演化

```console
$ archaeology structure core/app.py --history ~/projects/sample-project
core/app.py
History:     app.py -> core/app.py
Versions:    3 read, 0 could not be read

login  function
  created  2024-03-01 09:00:00 +0000  392cc0db  line 4
  modified 2024-03-06 09:00:00 +0000  8eaff71d  line 4

main  function
  created  2024-03-01 09:00:00 +0000  392cc0db  line 8

logout  function
  created  2024-03-06 09:00:00 +0000  8eaff71d  line 10
```

这里没有 `unchanged`。一份 history 是「一个 definition 发生变化的那些时刻」，什么都没
发生的版本不在其中——但快照里仍然存着它，所以上面的列表里 `main` 显示为 `unchanged`。

删除是推导的，不是从某一行里读出来的。`legacy.py` 在最后一个提交里被删掉了：

```console
$ archaeology structure legacy.py --history ~/projects/sample-project
legacy.py
Versions:    1 read, 1 could not be read
  78c0647a  the file was not in this commit

parse  function
  created  2024-03-01 09:00:00 +0000  392cc0db  line 4
  deleted  2024-03-10 09:00:00 +0000  78c0647a
```

数据库里没有任何一行写着 `deleted`，也不可能有：schema 不允许这个词。删掉这个文件的
那个提交本身也是它的一个版本——一个「文件不在这棵树里」的版本——删除就是从它两侧的
快照推导出来的。`--history --json` 输出的是同一批事实，每个事件都带着
`previous_commit_sha`，以及它跨过的 `blind_spots`。

读不出来的文件是不确定性，不是终结。下面这个仓库里，`broken.py` 在一个提交里是好的，
在下一个提交里有了语法错误：

```console
$ archaeology structure broken.py ~/projects/broken-project
broken.py
Version:     2024-05-02 09:00:00 +0000  f1f2a3f8

AST analysis unavailable: parse failed
  SyntaxError: invalid syntax (line 5, column 12)
```

```console
$ archaeology structure broken.py --history ~/projects/broken-project
broken.py
Versions:    1 read, 1 could not be read
  f1f2a3f8  SyntaxError: invalid syntax

ok  function
  created  2024-05-01 09:00:00 +0000  fec36049  line 1
      no ending: f1f2a3f8 could not be read, so whether it is still there is not known
```

解析失败了，所以这个文件里有什么是完全不知道的——连 `ok` 是否还在里面都不知道。工具
就照实这么说，而不是报一个它看不见的删除，也不是打印「0 个 definition」——那会被读成
「这个文件里什么都没有」。`SyntaxError:` 后面的话是解析器自己的原话，和说出它的 Python
版本一起存下来。

`structure` 只读 Python。路径不以 `.py` 结尾、历史里从未出现过这个路径、以及 `--history`
和 `--commit` 同时给出，这三种情况都会被拒绝并说明原因，而不是用一个看起来合理的答案
糊弄过去。

### 查看哪些文件总是一起变

有些文件是成组移动的：一个模块和它的测试、一份 schema 和读它的代码。`cochange` 只用
历史里已有的提交回答这个问题，它什么都不推断——不读提交信息、不读文件内容、也不管这些
文件是干什么的。

```console
$ archaeology cochange core/app.py
core/app.py
History:  app.py -> core/app.py

Analyzed: 3 commits of this file

No file changed alongside it.
2 pairs hidden: fewer than 2 shared commits.

The score is the share of this file's analyzed commits that touched the other file. Moving together is not a dependency, and a shared commit is not evidence of one.
```

分数是一个条件比率——**这个文件**的提交里有多大比例动到了对方——所以它不对称：先问
`core/app.py` 再问 `legacy.py`，是两个分母不同的问题。一对文件要共享两次提交才会被显示，
因为只共享一次是最弱的证据。`--min-shared 1` 会全部显示，而被隐藏的条数会打印出来而不是
让人去猜，所以「一条都没有」和「一条都没显示」始终是两个不同的答案：

```console
$ archaeology cochange core/app.py --min-shared 1
core/app.py
History:  app.py -> core/app.py

Analyzed: 3 commits of this file

 FILE        SHARED   SCORE
 ──────────────────────────
 README.md        1   0.333
 legacy.py        1   0.333

The score is the share of this file's analyzed commits that touched the other file. Moving together is not a dependency, and a shared commit is not evidence of one.
```

另一个旋钮是 `--large-commit-limit`。一次改动超过这个上限（默认 100）个文件的提交，会被
排除在所有样本之外——一个上千文件的提交是一次批量改动，而不是五十万条两两事实。被排除
的提交数会跟着答案一起给出：

```console
$ archaeology cochange core/app.py --min-shared 1 --json
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "path": "core/app.py",
  "files": [
    {
      "path": "core/app.py",
      "path_history": [
        "app.py",
        "core/app.py"
      ],
      "analyzed_commits": 3,
      "large_commits_excluded": 0,
      "hidden_pairs": 0,
      "co_changes": [
        {
          "path": "README.md",
          "shared_commits": 1,
          "score": 0.3333333333333333
        },
        {
          "path": "legacy.py",
          "shared_commits": 1,
          "score": 0.3333333333333333
        }
      ]
    }
  ]
}
```

**一个名字会为所有用过它的文件作答。** 上面那条 `History` 就是这个文件按顺序用过的名字，
旧名字下的提交属于同一个样本。删除之后被重新使用的名字会为两个文件作答，因为历史里两个
都在——JSON 里就是两条 entry。这和其他命令用的是同一套身份：`hotspots` 按文件而不是按
名字数提交，`file` 会为一个名字用过的每个文件作答。

co-change 是**关于提交的统计**，而块里那句话说清了它不是什么：两个文件一起动不是依赖关系，
共享一次提交也不是依赖的证据。它是一个该去看一眼的地方，不是一个可以下的结论。

### 解释一个提交

`explain` 是唯一会和模型说话的命令。它把一个提交的证据——其他命令读的全部东西，收成一份
bundle——组装起来，并在配置了模型时让它写出来。

**没有模型时它直接打印证据本身。**这是一个能用的答案，不是错误：这个工具首先是本地分析器，
模型是加在它上面的东西。

```console
$ archaeology explain 8eaff71d
No model is configured, so this is the evidence itself. Set CODEARCHAEOLOGY_AI_BASE_URL and CODEARCHAEOLOGY_AI_MODEL to have one explained.
{
  "commit": {
    "sha": "8eaff71d34a6f7eb6e27366ca8f72ab22cbba204",
...
  "absences": [],
  "bounds": {
    "co_change_partners": 5,
    "co_change_files": 5,
    "co_change_files_omitted": 0,
    "history_commits": 5,
    "history_commits_omitted": 0
  }
}
```

bundle 里装着：这个提交、它碰过的文件**以及改动落在这些文件的哪些行**（`ranges`，从 git
读出来——这是数据库里没有的那一样东西）、每个文件的一生、这次提交创建/修改/结束的
definitions、通常和这些文件一起变的文件、更早碰过它们的提交，以及一份"读不出来的是什么"
的清单。`bounds` 说明被截断的是什么，所以一个漏掉行的上下文不会读起来像完整的。

**和这个提交有关的 memory 会显示在证据旁边**，有自己的标题，永远不在 bundle 里面——见
[`explain` 会拿这些 memory 做什么](#explain-会拿这些-memory-做什么)。如果一条都没有，输出
和 memory 这一层出现之前完全一样。

**大到发不出去的提交，只会为模型做裁剪，不会为别的任何东西裁剪。**bundle 本身刻意不设上限
——文件列表提前截断会藏起被问的那个问题的答案，而读者可以往下翻——所以一个碰了一千个文件的
提交大约有 297,000 个估算 token。发给模型的是它的一份选样：最大的那些文件改动和 definitions、
这些文件各自的一生、各自最近的两条更早提交，以及每处改动最初的几段行范围。凡是丢过行的列表
都会说丢了多少行，写在提示词里的 `selection` 块中；行范围被裁过的文件会在自己那一行带上
`ranges_total`，所以一份残缺的证据不会读起来像完整的。这个命令打印的证据仍然是完整的，
`explain --json` 仍然带着全部，而答案是拿模型**实际看到的**去校验的——引用一个被丢掉的行的
答案会被拒绝，即使工具手里确实有它。实测：`benchmarks/context_benchmark.py` 里最宽的那个形状
——两百个文件、每个二十处 hunk 和十个 definitions——发出去约 11,000 token，而 bundle 是
358,000。

要让模型把它写出来，指定端点和模型：

```bash
export CODEARCHAEOLOGY_AI_BASE_URL=https://api.openai.com/v1
export CODEARCHAEOLOGY_AI_MODEL=gpt-4o-mini
export CODEARCHAEOLOGY_AI_API_KEY=...        # 或 OPENAI_API_KEY
```

端点需要说 OpenAI 的 chat 形状，而 OpenAI、DeepSeek、Ollama、vLLM 和 LM Studio 说的都是
这一种——把 `CODEARCHAEOLOGY_AI_BASE_URL` 指向本机 Ollama 就是完全本地的用法，不需要单独
的代码路径。密钥只从环境变量读，绝不做命令行参数，也绝不会出现在错误消息里。

其余设置同样是环境变量，没有配置文件：第二处放设置的地方，就是第二处可能不一致的地方。

| 变量 | 含义 | 默认 |
|---|---|---|
| `CODEARCHAEOLOGY_AI_BASE_URL` | 端点 | 无——要用模型就必须给 |
| `CODEARCHAEOLOGY_AI_MODEL` | 要问的模型 | 无——要用模型就必须给 |
| `CODEARCHAEOLOGY_AI_API_KEY` | 密钥，作为 bearer token 发送 | 回退到 `OPENAI_API_KEY` |
| `CODEARCHAEOLOGY_AI_TIMEOUT` | 单次调用整段的墙钟截止时间，秒 | 60 |
| `CODEARCHAEOLOGY_AI_MAX_RETRIES` | 首次之后的尝试次数，只用在重复能解决的失败上 | 2 |
| `CODEARCHAEOLOGY_AI_MAX_CONTEXT_TOKENS` | 发送内容的上限 | 无——超过就拒绝，而不是截断 |
| `CODEARCHAEOLOGY_AI_MAX_OUTPUT_TOKENS` | 答案的上限 | 无——被它截断的答案报告为"被截断"，不是"格式错误" |

截止时间是围绕整次调用的墙钟时间，不是 socket 超时，所以一个每次只吐一个字节的端点也会被结束。
重试覆盖连接与 DNS 失败、读超时、`429` 和 `5xx`；其余 `4xx` 是请求或密钥不对，重复它只会花你
的钱再失败一次。

答案分三个标题展示，而这三个标题就是读者分辨哪部分是哪种东西的全部依据：

- **Observed**（观察到）——证据里有的东西。每一行都引用 bundle，而引用了 bundle 里没有
  的东西会被拒绝，而不是被展示。
- **Possible**（可能）——候选原因，每一条都点名它依据的 observed changes。每条下面那个
  数字是有多少证据，不是这个理由有多好；它由工具数出来，从不由模型自报。
- **Unknown**（未知）——读不出来的，或者根本不在仓库里的。

**文本块开头第一句就说清它整份是什么：对证据的解读，不是"当时发生了什么"的记录。**这是
这一层要守的契约，也是读者需要在读到第一条论断**之前**就知道的那件事——一份关于某个提交的
答案，读起来很像那个提交的记录，而它不是。这个区别不是对模型水平的谦虚，而是工具真正能校验
的东西：引用是事实，引用周围的句子是解读，开头那句说明了哪一半被验证过。一句落在自己引用的
证据之内、却仍然把话说过了头的句子，程序抓不住——所以这句话是印出来的，而不是留给读者自己
判断。同一个提交解释两次，可能得到两份不同的解释，这就是"解读"的含义；两次之间必须完全一致
的是证据，不是文字。

**每一条结论都会把它依据的东西展开印出来**：提交、文件、改动落在的哪些行、碰到的
definitions、和它们一起变的文件、以及缺失项。引用会印在使用它的那条结论下面，也会再印一遍
在建立于其上的候选原因下面，所以读者永远不必顺着 id 回到 JSON 里去查某句话是凭什么说的。
**候选原因可以是错的；它做不到的，是让读者看不见它站在什么上面。**

一条引用可以命名六种东西之一，每一种都是拿 bundle 去核对的，而不是核对它的形状：`commit`、
`file`、`definition`、`range`（diff 真正落下的行段）、`cochange`（一对的一个方向，因为这个
统计量不是对称的）、`absence`。引用**能承载这个断言的最窄的那样东西**——是行段而不是整个
文件，是那一对而不是"这些文件"——这才是答案可被核对的前提。

**而候选原因会被标明它是什么。** 候选下面有一句话说明：解读不是发现——一起动不是依赖，一个
definition 出现或改变也不是关于作者意图的陈述——而一条**全部依据都只是共变统计**的候选，会在
自己那一行上说明这一点。一句话拦不住一个存心误导的模型；它拦的是**读者把候选当成发现**，
而这才是这一层真正能防住的那种失败。工具断然拒绝什么、只是标注什么、以及**根本抓不住**什么，
写在 `tests/test_boundaries.py` 里，模型每一种出错方式一节。

**模型永远不会被问作者为什么这么改**，因为证据里没有它，也没有任何措辞能让它变得可得。
候选原因就是候选，它绝不会被印在 summary 里——在那里一句猜测会被读成发现。违反这些规则的
答案会被再要一次，然后整份拒绝：什么都不展示，因为一份带注释的半截解释比没有更糟。

`--json` 把同一份答案交给程序。stdout 只有文档本身，所有提示和错误都走 stderr，所以调用方
不用先过滤就能解析 stdout。当提交大到发不出去时，文档会在证据旁边多一个 `selection`：证据
仍然是完整的全部，而这个键说明模型看到了其中多少。

```console
$ archaeology explain 8eaff71d --json
No model is configured, so this is the evidence itself. Set CODEARCHAEOLOGY_AI_BASE_URL and CODEARCHAEOLOGY_AI_MODEL to have one explained.
{
  "commit": "8eaff71d34a6f7eb6e27366ca8f72ab22cbba204",
  "state": "evidence_only",
  "explanation": null,
  "evidence": {
...
      "co_change_partners": 5,
      "co_change_files": 5,
      "co_change_files_omitted": 0,
      "history_commits": 5,
      "history_commits_omitted": 0
    }
  }
}
```

**两种状态同一个形状，由 `state` 说明是哪一种。** 没有模型时就没有解释，证据就是答案——所以
`state` 是 `evidence_only`，`explanation` 为 null。一个必须先搞清自己拿到哪种的读者，就是会
出错的读者，这和 `file --json` 永远是一个装着列表的对象是同一个理由。证据和答案并排放在一起，
所以不必再调一次就能互相核对；JSON 里的 `confidence` 是工具数出来的那个数，不是模型自己选的。

### 记住人知道的事情

`memory` 是这个工具里唯一不是从仓库推导出来的部分。它存放人对项目说过的话——某条规则
为什么存在、某个约束是为了什么、什么试过并且放弃了——存在证据旁边，**永远不把这些话当成
证据**。这一层完全不需要模型：写一条、列出来、读回来，全程没有任何 endpoint，这正是它的
意义。它也是数据库里唯一一样 git 给不回来的东西——证据表是缓存，它不是，这一点写在下面的
"数据库放在哪"一节里。

```console
$ archaeology memory create "We keep the login helper in one module: the split state machine caused an incident." --about-path core/app.py --cite-commit 78c0647a --cite-file core/app.py --since-commit 392cc0db ~/projects/sample-project
Memory      3f2a9c1e-8b47-4d6a-9f10-2c5e7a0b41d9
Subject     core/app.py
Evidence    2 citations
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

一条 memory 只关于一件事，四种对象就是四个 flag：整个项目（`--about-repository`）、一个
文件（`--about-path`）、文件里的一个 definition（`--about-path` 配 `--about-definition`）、
或者一个提交（`--about-commit`）。subject 必须在已存的历史里能解析——历史从没碰过的路径
会被拒绝，用的是 `files` 已经用过的那句话——因为没人找得到的 memory 就是没人会读的
memory。

它是从什么来的则是可选的，写法一样，一种证据一个 flag：`--cite-commit`、`--cite-file`、
`--cite-definition`、`--cite-range`（必须配一个 `--cite-commit`，因为 range 是某一个提交
diff 里的一段）、`--cite-cochange`、`--cite-absence`。**每一条 citation 在写入之前都会先
对着已存的历史核一遍**，所以 memory 不可能指向不存在的东西。没有任何 citation 的 memory，
就是一条明说自己没有的 memory。

**这句话从什么时候开始成立，由作者来说，而且可以不说**：`--since-commit`（一个 sha 或它
的前缀，动作之前会对着已存的历史解析）或者 `--since-date`（`YYYY-MM-DD`），两个不能一起
给——一条 memory 只有一个起点。不写就是未知，永远不是"从一开始"；而 `explain` 的
*in force at this commit* 就是由它算出来的。关于项目的声明只被记录、从不被推断：没人写下
日期的规则就一直是没有日期的，直到有人开口。

### 列出 memory

```console
$ archaeology memory list ~/projects/sample-project
Memories (2)

1. core/app.py
   We keep the login helper in one module: the split state machine caused an incident.
   b7d4e2f1  active  admitted 2024-06-01 09:00:00 +0000  since 392cc0db21a7
   evidence: commit 78c0647a, file core/app.py

2. the project
   We do not add runtime dependencies casually.
   5e8a1c47  active  admitted 2024-06-01 08:00:00 +0000
   evidence: none attached

A memory is what a person stated about this project: the tool did not derive it and cannot check it. The citations are checked; the statements are not.
```

`--limit` 和 `--all` 和别处一样，排序是最新的在前。短 id 就是 `show` 接受的写法；列表里放
不下的长陈述会被截断，而且截断这件事本身会写出来，并指明哪条命令能读到全文。

### 查看单条 memory

```console
$ archaeology memory show b7d4e2f1 ~/projects/sample-project
Memory      b7d4e2f1-9c3a-4e58-8f02-1a6c5d9b7e34
Repository  /home/you/projects/sample-project
Subject     core/app.py
Author      Ada Lovelace <ada@example.com>
Admitted    2024-06-01 09:00:00 +0000
State       active
Since       392cc0db21a7 (2024-03-01)

We keep the login helper in one module: the split state machine caused an incident.

Evidence (2)
  commit      78c0647a  resolved
  file        core/app.py  resolved

Lifecycle   nothing supersedes it

A memory is what a person stated about this project: the tool did not derive it and cannot check it. The citations are checked; the statements are not.
```

`Since` 是这条陈述从什么时候开始成立，没人说过时就是 `unknown`——永远不是这条 memory
被写下的时间，那是关于数据库的事实，不是关于项目的。`Author` 是仓库 git 配置给出的身份，
配置里没有时就是 `unknown`。

每条 citation 都会被重新读一遍，状态用文字写出来：`resolved`；`no longer in this
history`——一次 rewrite 删掉了某个提交，并不代表这条 memory 错了，工具也永远不会去改人
写过的话；或者 `not checked`，这是列表对 range 和 co-change 的说法，因为它们分别要付出一
次 diff 和一次走遍历史的代价，而这个代价由 `show` 来付。**subject 也按同样的方式检查，
而且两种视图都检查**：一条关于某个文件的 memory，如果那个文件被一次 rewrite 从历史里拿掉
了，它旁边就会写着 `(no longer in this history)`，而不是照旧打印，让人以为它还在。

```console
$ archaeology memory list --json ~/projects/sample-project
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "memories": [
    {
      "memory_id": "b7d4e2f1-9c3a-4e58-8f02-1a6c5d9b7e34",
      "repository": "/home/you/projects/sample-project",
      "statement": "We keep the login helper in one module: the split state machine caused an incident.",
      "author": {
        "name": "Ada Lovelace",
        "email": "ada@example.com"
      },
      "admitted_at": "2024-06-01T09:00:00+00:00",
      "state": "active",
      "subject": {
        "kind": "path",
        "path": "core/app.py",
        "resolution": "resolved"
      },
      "since": {
        "commit": "392cc0db21a7a299e0457555a38c1cfeef7578a3",
        "commit_date": "2024-03-01"
      },
      "ended_at": null,
      "end_reason": null,
      "superseded_by": null,
      "supersedes": null,
      "citations": [
        {
          "kind": "commit",
          "ref": "78c0647a",
          "resolution": "resolved"
        },
        {
          "kind": "file",
          "ref": "core/app.py",
          "resolution": "resolved"
        }
      ]
    },
...
  ]
}
```

`--json` 会给出这个仓库的全部 memory，不管 `--limit` 说了什么——一个要列表的程序要的是
全部，这是 `files --json` 早就定下的规则。

### 替换一条 memory

一条说错了、或者已经不再成立的 memory，既不会被编辑也不会被删掉：用一条新陈述替换它，
被替换的那条留在原地。

```console
$ archaeology memory supersede b7d4e2f1 "The login helper stays in one module, and it checks the session itself." --about-path core/app.py --cite-file core/app.py ~/projects/sample-project
Memory      3f2a9c1e-8b47-4d6a-9f10-2c5e7a0b41d9
Supersedes  b7d4e2f1-9c3a-4e58-8f02-1a6c5d9b7e34
Subject     core/app.py
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

一次动作写两行：后继者按与 `create` 完全相同的 subject 与 citation 规则写入，被替换的
那条则带着时刻和一条指向后继者的链接关闭。**没有任何东西被覆盖**——对旧 id 执行 `show`
仍然打印当时写下的那句话，并用 `superseded by` 指出后继者——而且已经结束的 memory 不再
接受任何动作：拒绝时会说请另记一条新的。链式替换就是一行行的链，而且只可能向前指，因为
后继者永远是这次动作刚刚写下的那条 memory。

### 结束一条 memory

`invalidate` 是 memory 结束的另一种方式：没有后继者，但必须给出理由。

```console
$ archaeology memory invalidate 5e8a1c47 --reason "we allow one dependency now" ~/projects/sample-project
Memory      5e8a1c47-3f92-4b6d-a1e8-7c2f4d9a6b53
State       invalidated
Reason      we allow one dependency now
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

"它错了"和"它不再适用"是两句不同的话，所以理由跟着 memory 一起留着，而不是留给读者去猜。
一条规则重新生效时，那是一条**新的** memory——记录于是读作"生效、失效、再生效"——因为把
一行"解除结束"会丢掉中间那一段。

两种结束状态默认都不出现在 `memory list` 里，因为默认的问题是项目**现在**知道什么：

```console
$ archaeology memory list --include-ended ~/projects/sample-project
Memories (3)

1. core/app.py
   We keep the login helper in one module: the split state machine caused an incident.
   b7d4e2f1  active  admitted 2024-06-01 09:00:00 +0000  since 392cc0db21a7
   evidence: commit 78c0647a, file core/app.py

2. the project
   We do not add runtime dependencies casually.
   5e8a1c47  active  admitted 2024-06-01 08:00:00 +0000
   evidence: none attached

3. core/app.py
   The health check belonged in core/app.py.
   a1f3d8c2  invalidated 2024-06-01 07:30:00 +0000: it moved into its own module
   evidence: none attached

A memory is what a person stated about this project: the tool did not derive it and cannot check it. The citations are checked; the statements are not.
```

### 仓库搬家的时候

数据库的文件名是仓库绝对路径的摘要，所以换到新路径的检出会拿到一个新的空数据库，memory
看起来"消失了"。其实没有：旧文件还在缓存目录里，每一行都在。工具自己找不到它——没有任何
东西告诉它这两个路径是同一个项目，而靠 remote URL 或某个 commit sha 去猜，正是这个项目
拒绝做的那种推断——所以回来的路是显式的，一共三步：

```bash
# 1. 把旧文件指向新路径（这一步重写的是证据，不是 memory）
archaeology analyze ~/projects/sample-project --db ~/.cache/codearchaeology/<old>.db

# 2. memory 会被保留并报告出来，在 adopt 之前写入会被拒绝
archaeology memory list ~/projects/sample-project --db ~/.cache/codearchaeology/<old>.db

# 3. 说清楚：旧路径就是这个项目，只是换了个位置
archaeology memory adopt --from ~/old/sample-project ~/projects/sample-project --db ~/.cache/codearchaeology/<old>.db
```

`adopt` 是第六个动作，它只改 memory 的一件事：它属于哪个仓库。陈述、作者、时间、citation
和状态都按写下的样子保留，而且这个动作会被记进数据库，免得以后看起来像这些行一直属于这个
检出。

```console
$ archaeology memory adopt --from ~/old/sample-project ~/projects/sample-project
Adopted     2 memories
From        /home/you/old/sample-project
To          /home/you/projects/sample-project
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

**路径要写全，而且没有交互式确认。** 把路径敲出来本身就是确认——所以打错一个字就匹配不
上，所以工具仍然可以脚本化。它会拒绝：什么都没为它写过的路径、它自己现在的路径、以及目标
库已经有自己 memory 的情况——合并两个项目的知识，这个版本的回答是拒绝，而不是猜。

`analyze` 在即将把 memory 留在视野之外时会说明——共享 `--db` 或一次搬家造成的那一刻——
报出数量、路径，以及补完这件事的动作。工具永远不会销毁任何东西：只有删掉文件才会。

### 把 memory 取出来

到这儿为止，memory 只存在一个地方：缓存目录里那个以仓库路径摘要命名的数据库文件。备份检出
目录不会备份到它，换一个路径的检出又会去找另一个文件——所以 `memory export` 把 memory 写成
一个由你自己放置的文本文件，它们跟着你把它放到哪里。

```console
$ archaeology memory export ~/projects/sample-project --output memories.jsonl
Wrote       3 memories
To          /home/you/projects/sample-project/memories.jsonl
Repository  /home/you/projects/sample-project
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

不给 `--output` 时，整个文档写到标准输出，所以可以用你自己的重定向接上：

```bash
archaeology memory export ~/projects/sample-project > memories.jsonl
```

**文件是 JSON Lines 格式**，第一行是头部：

```text
{"memory_export_version": "1", "repository": "/home/you/projects/sample-project", "count": 3, "exported_at": "2026-10-08T09:00:00+08:00"}
```

`memory_export_version` 是读取方校验的版本，`repository` 是将来 import 要指认的路径，`count`
是后面跟着多少行 memory，`exported_at` 是给你看的。之后**一行一条 memory，按 `memory_id`
升序**——这是一个全序，而写入顺序给不了全序，因为两条 memory 可能共享同一个时间戳：

```text
一条 memory
├── memory_id        完整 UUID：文件就是按它排序的
├── repository       和头部同一个路径，每一行都有
├── statement        本人原话，完整；里面无论有什么，都落在一行里
├── author           {"name", "email"}；工具判断不出来时是 null
├── admitted_at      这个动作发生的时间
├── state            active | superseded | invalidated
├── subject          {"kind", "path"?, "qualname"?, "commit"?}
├── since            {"commit"} 或 {"date"} 或 null —— null 表示不知道
├── ended_at         memory 还是 active 时为 null
├── end_reason       被 invalidate 时结束的理由
├── superseded_by    接替者的 id，或者 null
└── citations        [{"kind", "ref"}]，按写入时的顺序
```

**有四个字段在别处会显示、但刻意不进文件**，每一个都有各自的理由。subject 的解析结果和
citation 的解析结果，都是证据层针对**这个**仓库的历史算出来的答案——换到另一个仓库，它们就是
另一个问题的答案，那比没有答案更糟。memory 起点那个提交的日期，是从已存的 commits 里读出
来的，收到文件的那个仓库会重新读一遍，或者读不到。而 supersede 的反向链接不在文件里，是因为
存储只保留一个方向，好让两行没有机会互相矛盾——而文件也是一种存储。

这个取舍带来的东西比省掉的更多：**写导出完全不读证据层。** 它不会被过期的分析误导，不检查
任何 citation，而且在坏掉的恰好是证据的时候，它仍然是你拿得到的东西。

**目标文件已存在就拒绝**，除非加 `--force`：

```text
Error: /home/you/projects/sample-project/memories.jsonl exists; this command
does not replace a file it did not write. Give another path, or pass --force to
replace it
```

这个工具在任何地方都不做交互式询问——一个问题就会破坏所有用它写的脚本——所以安全性是一个
标志位加一句话，就像 `adopt` 的安全性是你必须完整敲出来的那个路径。

**同一个 store 导出两次，除了 `exported_at` 之外完全一致。** 每一行逐字节相同，因为顺序是集合
的函数、而且行里没有任何字段是推导出来的——所以两次导出之间的 `diff` 只显示 memory 变了什么，
别的什么都不显示。

**这个文件是一份拷贝，不是第二个记录源。** 编辑它不会有任何影响，直到有东西 import 它；其余
所有命令读的都是数据库。

### `explain` 会拿这些 memory 做什么

`explain` 读的是一个提交，和它相关的 memory 会出现在证据**旁边**——同一个输出里、有自己的
标题，而且**永远不在证据 bundle 里面**。一条 memory 算相关，当它是关于这个提交的、是关于
这个提交改动的某个 definition 的、是关于它碰过的某个文件的（那个文件曾经叫过什么名字都
算）、是关于整个项目的，或者它引用了这个提交。subject 越具体越靠前，然后按 admitted 时间
由新到旧排，被上限截掉的全部计数报出来。

它们分成两组，这个分法本身就是重点：**in force at this commit** 是代码写下时确实成立的
说法；**related, not provably in force** 是其余全部——没人记下起点的，或者时间范围落在
这个提交之外的。把第二组藏起来就是藏知识；把两组并成一组，就会让一条 2026 年的规则读起来
像是 2023 年那个提交的原因。

```console
$ archaeology explain 78c0647a --json
No model is configured, so this is the evidence itself. Set CODEARCHAEOLOGY_AI_BASE_URL and CODEARCHAEOLOGY_AI_MODEL to have one explained.
{
  "commit": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "state": "evidence_only",
  "explanation": null,
  "evidence": {
...
  "memory": {
    "in_force": [
      {
        "memory_id": "b7d4e2f1-9c3a-4e58-8f02-1a6c5d9b7e34",
        "repository": "/home/you/projects/sample-project",
        "statement": "We keep the login helper in one module: the split state machine caused an incident.",
        "author": {
          "name": "Ada Lovelace",
          "email": "ada@example.com"
        },
        "admitted_at": "2024-06-01T09:00:00+00:00",
        "state": "active",
        "subject": {
          "kind": "path",
          "path": "core/app.py",
          "resolution": "resolved"
        },
        "since": {
          "commit": "392cc0db21a7a299e0457555a38c1cfeef7578a3",
          "commit_date": "2024-03-01"
        },
        "ended_at": null,
        "end_reason": null,
        "superseded_by": null,
        "supersedes": null,
        "citations": [
          {
            "kind": "commit",
            "ref": "78c0647a",
            "resolution": "resolved"
          },
          {
            "kind": "file",
            "ref": "core/app.py",
            "resolution": "resolved"
          }
        ]
      }
    ],
    "not_provably_in_force": [
      {
        "memory_id": "5e8a1c47-3f92-4b6d-a1e8-7c2f4d9a6b53",
        "repository": "/home/you/projects/sample-project",
        "statement": "We do not add runtime dependencies casually.",
        "author": {
          "name": "Ada Lovelace",
          "email": "ada@example.com"
        },
        "admitted_at": "2024-06-01T08:00:00+00:00",
        "state": "active",
        "subject": {
          "kind": "repository",
          "resolution": "resolved"
        },
        "since": null,
        "ended_at": null,
        "end_reason": null,
        "superseded_by": null,
        "supersedes": null,
        "citations": []
      }
    ]
  }
}
```

`memory` 是 `evidence` 的兄弟键，永远不是它内部的一个键；而且**没有内容可显示时它就不
存在**——一个提交如果它的文件上没有任何 memory，打印出来和 v0.4 一模一样。里面的每条
memory 就是 `memory show --json` 打印的那个对象，所以程序在哪里读到它，形状都一样。

**模型看到的是同一份 section，而且带一个说明它是什么的标题**，指令也告诉它可以拿它做
什么：memory 可以支撑一个候选原因，但永远不能被当成证据引用、也不能被写成观察到的事实。
答案可以按 id 指向某条 memory，这个 id 会对照模型真正看到的那份 section 检查。然后陈述
由工具从库里原样打印——不是模型的转述——模型那句话跟在下面，标明那是它的解读。

### 重新跑 `analyze` 不会把结构清掉

`analyze` 和 `ast` 写的是不同的表，而 `analyze` 只删除 git 已经没有的东西：一次 rebase
或 amend 会把它带走的提交，连同属于这些提交的文件版本和 definitions 一起移除；还成立
的东西都留着。例外是数据库里新出现的提交——在 `ast` 再跑一次之前它没有结构，`structure`
会说清楚该跑哪条命令：

```console
$ archaeology analyze ~/projects/sample-project
Repository  /home/you/projects/sample-project
Commits     7 (10 file changes)
Range       2024-03-01 to 2024-03-12
HEAD        07968666
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
$ archaeology structure core/app.py ~/projects/sample-project
core/app.py
History:     app.py -> core/app.py
Version:     2024-03-12 09:00:00 +0000  07968666

AST analysis unavailable: no version was stored for this commit
  run 'archaeology ast' to read the file versions git holds
$ archaeology ast ~/projects/sample-project
Repository   /home/you/projects/sample-project
Versions     7 file versions, 17 definitions
Parsed       1 parsed, 6 reused, 0 could not be parsed
Database     /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

第二次 `ast` 只解析了那一个新版本，复用了已经读过的六个——正是 `analyze` 留下的那六个。
如果 `analyze` 把结构扔掉了，这一行会变成 `7 parsed, 0 reused`。唯一会清掉它的是 schema
重建，而那是在造一个新库，不是对同一个库重扫。重建也正是"缓存例外"存在的理由：重建会
丢掉每一张"行能从 git 重新拿到"的表，而 memory 的表不在那张名单上，所以库里的 memory
会活过重建。

### 数据库放在哪

数据库文件名是仓库绝对路径的摘要，所以同一个项目的两份检出各有一份库。

| 平台 | 位置 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\codearchaeology\` |
| macOS | `~/Library/Caches/codearchaeology/` |
| Linux 及其他 | `$XDG_CACHE_HOME/codearchaeology/`，没有该变量时用 `~/.cache/codearchaeology/` |

设置环境变量 `CODEARCHAEOLOGY_CACHE_DIR` 可以换地方，或者给任意命令加 `--db` 直接
指定文件。

**数据库不再只是一份缓存了，这是 v0.5 要带走的那句话。** 工具推导出来的一切都是仓库之上
的一层缓存：每一行证据都能靠重读 git 重建，所以 schema 换代时旧库的证据直接丢掉重读就行。
**memory 行什么也重建不了**——那是人说过的话——而它就在同一个文件里。所以 schema 重建会
把它们留着，而删掉这个文件就会把它们删掉：替换它之前先复制一份。工具读不出一个数据库时
会这么说，原因就是这个。一个数据库只装一个仓库的 memory：把 `analyze` 指向另一个仓库的库
会接管证据、但把 memory 留下，此后 memory 命令会说明这些 memory 属于谁，而不是把它们混
进来。

## 性能

`benchmarks/benchmark.py` 测量读侧的命令和 AST 这一趟，规模由你指定；它会把这一趟跑
三次：在一个没有结构的库上、紧接着在同一个库上、以及在一个刚多了一百个提交的库上。它是
**手工跑的，不进 CI**——断言墙上时钟的测试在机器一忙就会失败，只会教人重跑。

```console
$ uv run python benchmarks/benchmark.py --commits 10000 50000 100000 200000
```

在一台机器上、跑一份生成的历史、每个数字取三次里最快的一次：

| 提交数 | 文件改动 | 仓库 | 数据库 | `analyze` | `timeline` | `hotspots` | `file <path>` | 仅按名字 |
|---|---|---|---|---|---|---|---|---|
| 10,000 | 50,500 | 8.5 MB | 12.4 MB | 1.4s | 0.24s | 0.36s | 0.39s | 0.03s |
| 50,000 | 250,500 | 42 MB | 62.6 MB | 7.8s | 1.7s | 2.3s | 2.3s | 0.13s |
| 100,000 | 500,500 | 84 MB | 125 MB | 17.1s | 3.5s | 5.6s | 5.6s | 0.27s |
| 200,000 | 1,000,500 | 168 MB | 250 MB | 41.0s | 8.6s | 13.6s | 13.3s | 0.61s |

**历史是生成的，所以看曲线，不要看具体数字。** 200 个文件，每次提交改其中 5 个，没有
改名也没有删除，所以每次提交的大小是齐的。真实仓库的 diff 更大、改名更多、文件大小的
长尾更长，这三样都会移动这些数字。

曲线说明了什么：

- **没有任何一处是平方复杂度。** 从一万提交到二十万，每个操作的耗时大致随历史翻倍。
- **`analyze` 是主要开销，而且只付一次。** 从 git 里把历史读出来、再写进 SQLite，比
  之后所有查询加起来还贵——而在二十万提交时，其中 85% 是写入：重扫会把每个提交、每个
  父提交、每一行文件改动都重写一遍，不管有没有变。它之后的一切都是读数据库。
- **`hotspots` 和 `file <path>` 一样贵**，因为一个文件的一辈子只能靠走完整张提交图才能
  重建，而这两条都需要。这不是巧合、也不是该被优化掉的东西，这是模型本身的形状。
- **按名字问比按身份问便宜一到两个数量级**，因为前者是一条查询，后者是一次遍历。它们
  也确实是两个不同的问题：按名字回答的是「这个路径」，按身份回答的是「这个文件跨过它
  所有改名的历史」。

### memory 存储，单独测过

`benchmarks/memory_benchmark.py` 单独测量这一层，规模由你指定：

```console
$ uv run python benchmarks/memory_benchmark.py --sizes 10 100 1000 10000 100000
```

| Memories | 数据库 | `create` | `memory list` | `memory list --json` | `memory show` | `explain` 的 section |
|---|---|---|---|---|---|---|
| 10 | 120 KB | 14.0ms | 35.0ms | 37.3ms | 35.7ms | 0.5ms |
| 100 | 156 KB | 13.8ms | 36.4ms | 41.1ms | 33.9ms | 1.0ms |
| 1,000 | 484 KB | 14.5ms | 37.6ms | 76.6ms | 33.1ms | 8.2ms |
| 10,000 | 3.7 MB | 14.1ms | 40.2ms | 453ms | 35.0ms | 82ms |
| 100,000 | 37.2 MB | 20.4ms | 78.4ms | 4.74s | 46.6ms | 1.11s |

`create` 是一次准入，取二十次的批量计时；其余是整个命令或整个调用，取三次里最快的一次。
一条 memory 约占 380 字节。

- **写入不会越来越贵。** 十条时约 14ms，十万条时约 20ms——而且其中大部分是这个动作自己的
  检查，不是插入本身。
- **读一条也不会。** `memory show` 是平的，因为它按 id 查一行。
- **`memory list` 现在也是平的，这是量出来的结果。** 它原来是线性的——一万条时 843ms——
  因为它为了打印二十条而把整个库读回来。现在读取在切片处就停，后面的数量单独数；子行
  （citation、反向的生命周期链接）按"真正读到的那些 id"去取，而不是重新扫一遍整个库。
  十万条时，切片读取从 495ms 降到 0.1ms，整库读取从 748ms 降到 340ms。
- **`--json` 按约定就是线性的。** 一个要列表的程序要的是全部，所以十万条 memory 是 4.74s、
  一个几十兆的文档。修法是 subject 过滤，而 v0.5.0 之后的审计**把它的优先级调后了**：
  它不是排在什么之前，因为会疼的那个量级只有 import 才造得出来——一千条时这里是 76.6ms。
  理由和数字在 `docs/v0.5-product-audit.md` §8。
- **`explain` 每次要为整个库付一次钱。** 选出与某个提交相关的 memory，会把这个仓库的全部
  memory 读出来在 Python 里过滤，因为六种关系里有"文件曾经的名字"和"definition 的名字"，
  光靠 schema 查不出来。十万条时这是在一条本来就要重建上下文的命令上再加 1.11s。修法是
  一条 SQL 预过滤，优先级和上一条相同、理由也相同：一千条时是 8.2ms。

### 结构层，单独测量

`ast` 是工具里昂贵的那一半，所以 benchmark 按阶段计时，并把这一趟跑三次：**cold**（库
里还没有结构）、**warm**（紧接着在同一个库上，每个版本都已存好）和 **grown**（追加一百
个提交、跑 `analyze`、再跑这一趟）。同一份 fixture，Python 3.13：

| 提交数 | 文件版本 | Definitions | 数据库（Git） | 数据库（含 AST） | cold | warm | grown |
|---|---|---|---|---|---|---|---|
| 10,000 | 50,500 | 272,700 | 12.4 MB | 101.3 MB | 33.0s | 10.0s | 9.0s |
| 50,000 | 250,500 | 1,352,700 | 62.6 MB | 505.5 MB | 186.9s | 46.3s | 46.8s |
| 100,000 | 500,500 | 2,702,700 | 124.9 MB | 1008.8 MB | 395.3s | 109.6s | 134.5s |
| 200,000 | 1,000,500 | 5,402,700 | 249.5 MB | 2016.1 MB | 809.9s | 304.0s | 333.7s |

- **重跑不解析、不写入，但仍然要把一切都读一遍。** warm 这一趟复用了它存过的每个版本，
  一行都没有交给写入器，却仍然要花掉第一趟的三分之一：它要把每个 blob 从 git 里读出来
  才知道它就是存过的那个，还要把每个存下的 definition 读回来比较。省下的是解析和写入。
- **多了一百个提交之后的那一趟，就写这一百个**——二十万提交时是一百万个版本里的 500 个
  ——耗时与 warm 差不多，差值主要是中间那次重扫把这一趟要用的页面挤出了操作系统缓存。
- **除写入之外，每个阶段都与文件版本数成线性。** 写入速度从约 52,000 行/秒降到约
  20,000 行/秒（数据库涨过 page cache 之后），但总时间仍然是线性的。
- **遍历树比它前面的解析更贵。** 把每个 definition 渲染出来并哈希，代价大约是解析它
  所在文件的 3.5 倍，各个规模上都是如此。
- **快照存储的行数约是一份变更日志的三倍**（这份 fixture 上是 2.98–3.00），结构层占用的
  存储大约是 Git 事实的八倍：二十万提交时，250 MB 的 Git 事实会变成 2 GB 的数据库。
- **二十万提交的历史跑这一趟大约需要 4 GB 内存**，因为存下的版本和存下的 definitions
  在工作期间都是整份持有的。要拿这个数字做规划，而不是数据库体积。

这一趟会自己报告成功率——一万提交时是 `parsed 45000, failed 5000`，10.0%，正是 fixture
里十分之一文件不可解析的比例——而且每一行都带着读它、分析它的那个生产者。

## 项目结构

```
src/codearchaeology/
    cli.py          Typer 应用与它的各个命令
    analysis.py     一次分析：读仓库、写数据库
    ast_pass.py     AST 这一趟：读每个 Python 文件版本，存下它的结构
    cache.py        分析数据库放在哪
    history.py      调用 git 并把它的输出解析成 Commit 对象
    objects.py      从 git 里读文件内容，一次读很多个
    definitions.py  一个文件版本的 definitions，从它的字节里读出来
    definition_history.py  从快照推导每个 definition 的一辈子
    structure.py    结构视图：一个版本、一份 history，以及两者的 JSON
    storage.py      SQLite 表结构与查询
    timeline.py     时间线视图：行数据、表格、JSON
    commit.py       单个提交的视图
    file.py         单个文件的视图：文本块与 JSON
    cochange.py     co-change 分析：统计量、文本块与 JSON
    lifecycle.py    从存下来的历史里重建每个文件的一辈子
    statistics.py   汇总一个文件一辈子的那些数字
    hotspots.py     给活着的文件排名，以及列出每个文件的清单
    relationships.py 提交与文件的关系，从任一端都能读
    context.py      一个提交的证据，构建时模型完全不参与
    selection.py    证据大到发不出去时，模型看到其中的哪些
    explanation.py  答案的形状、给模型的指令，以及文本块
    validation.py   决定一份答案能不能展示的三趟检查
    provider.py     工具里唯一被允许触网的模块
    memory.py       memory 存储：表、动作，以及版本戳
    memory_checks.py  把一个 subject 和一条 citation 对着证据核一遍
    memory_view.py  memory 的文本块、JSON 对象，以及结尾那句话
    memory_section.py  哪些 memory 属于某个提交旁边，以及它们怎么展示
    memory_export.py  把一个仓库的 memory 写成可携带的文本文件
    formatting.py   两个视图共用的小工具
tests/
    sample_repo.py  构造一个确定性的小仓库供测试使用
    conftest.py     把这个仓库交给每个测试的 fixture
benchmarks/
    benchmark.py    Git 各操作与 AST 这一趟，规模由你指定
    cochange_benchmark.py  co-change 分析随历史增长的成本
    index_benchmark.py     每个数据库索引买到了什么、花了什么
    context_benchmark.py   解释用的 bundle 有多大，按 section 拆开看
    memory_benchmark.py    memory 存储随规模增长的成本，从十条到十万条
```

## 核心原则

以下八条不是口号，它们约束代码被允许怎么写。

1. **Core First** —— 核心不能依赖 LLM。任何功能都必须能完全不用 AI 跑通。
   AI 只是上面的一层，永远不是地基。由 `tests/test_offline.py` 用两种方式守住：
   每条读取命令都在"打开 socket 的所有途径被拿掉"的情况下跑一遍；并且整个包里
   只有 `provider.py` 允许 import 网络模块——所以以后有单元在过高的层次上伸手去够
   网络，会在构建时就失败。
2. **Local First** —— 任何 Git 仓库都必须能在本地分析，不依赖远程服务。
3. **Evidence First** —— 任何结论都必须能追溯到具体的 commit / diff / AST 节点。
   禁止凭空推测。
4. **Python-only v0.x** —— AST 分析读的是 Python。
   多语言支持在路线图上，不在当前范围内。
5. **先 CLI，后 Web** —— v0.x 的界面就是命令行，没有 Web UI。
6. **SQLite 是唯一存储** —— v0.x 不引入 PostgreSQL / Redis / 向量数据库。
7. **Interpretation，不是 history** —— 工具从仓库里推导出来的是事实：一个提交、一段
   diff、一个 definition，每一样背后都有测试。而模型基于这些事实写出来的东西，是**对它们
   的解读，绝不是"当时发生了什么"的记录**，两者永远不会被当成同一类东西呈现。证据是可
   复现的，文字不是；结构把 Observed / Possible / Unknown 分开；每条论断都印出它依据的
   引用；文本块开头就说明自己是什么；模型写的任何东西都不会被当成证据存下来。由
   `tests/test_explain.py`（开头那句，以及它不出现在 JSON 里）和结构本身守住——而结构是否
   还成立，由 `tests/test_validation.py` 和 `tests/test_boundaries.py` 看着：解读无法被伪装成
   观察，引用不到东西的答案会被整份拒绝。完整表述在 `docs/v0.4-problem-definition.md` §12。
8. **memory 是人说过的，永远不是证据** —— 库里唯一不是工具推导出来的部分：由人用明确
   的动作写下的陈述，连着它关于什么、以及它是由哪些证据来的（可以一条都没有）。它显示
   在证据**旁边**，永远不在证据里面；模型会被告知它是什么；答案可以按 id 指向某一条，
   但永远不能把某一条当成事实来引用；模型写的任何东西都不会被存进库里。五条硬规则写在
   `docs/v0.5-design.md` §13.2，边界本身由 `tests/test_memory_boundaries.py` 守住：
   每条证据侧命令都在 memory 库读不出来的情况下跑一遍；每条读取命令都和"库里一条 memory
   都没有"的数据库逐字节对比；只有命令行能调用写入动作；memory 的 id 当作六种证据里的
   任何一种都会被拒绝。**反方向也是关死的**：模型的答案可以指向一条 memory，却永远不能
   创建或修改一条——`provider.py` 不从包里 import 任何东西，所以"模型永远不写 memory"
   是关于 provider *是什么* 的一句话，而不是它遵守的一条规则。

## 路线图

| 阶段 | 增加什么 | 状态 |
|---|---|---|
| v0.1 | Git 扫描、提交历史、文件改动、SQLite 存储、CLI 时间线 | 已完成 |
| v0.2 | 文件生命周期、代码热点，以及给别的程序读的 JSON 输出 | 已完成 |
| v0.3 | AST 分析、函数与类的演化，以及 co-change | 已完成 |
| v0.4 | 基于证据层的 AI 解释（Provider 可替换） | 已完成——`explain`，带 OpenAI 兼容的 provider |
| v0.5 | Developer Memory —— 人知道的事情，存在证据旁边 | 已完成——`memory create`、`list`、`show`、`supersede`、`invalidate`、`adopt`，`explain` 会把相关的那些显示在证据旁边 |
| v0.6 | AI 参与的改动分析与回放 | 计划中 |

每个阶段一次只做一个，前一个稳定之前不设计后一个。

每个版本以及它改了什么，记录在 [CHANGELOG](CHANGELOG.md) 里。

## 已知限制

- **memory 是陈述，不是事实。** 工具没有推导它，也无法核对它；工具核对的是引用，而一条
  引用解析成功并不代表那句话是真的。memory 说的任何东西都不会被当成证据，而唯一会读
  memory 表的命令是 `explain`——它把 memory 放在证据旁边、有自己的标题，永远不在 bundle
  里面。
- **memory 的起点是一个声明，而大多数 memory 都没有。** `--since-commit` 和
  `--since-date` 就是一个人给出它的方式，而且它们按设计就是可选的：没人写下日期的
  memory 显示在 "related, not provably in force" 下面，而不是被假定为从一开始就成立。
- **模型可以把一条 memory 改写成一条观察，而没有任何检查能抓住它。** 规则是 memory 可以
  支撑一个解读、永远不能变成一个观察；工具能守住的是结构——section 是分开的、citation
  是被核对的、陈述是从库里原样打印的。而一句把 memory 转述成 observed change、旁边还挂着
  一个真实 citation 的话，能通过现有每一道检查。`tests/test_memory_boundaries.py` 把这一点
  钉成一个已知的限制，而不是留给以后去发现。
- **一条 memory 有两种结束方式，没有任何编辑方式。** 被新陈述替换（`supersede`），或者
  带着理由结束（`invalidate`）；没有删除、没有原地编辑，而且两种状态都是终态——规则重新
  生效是一条新的 memory。写错的陈述就用 invalidate 并说明写错了。
- **一条 memory 只存在一个地方。** 库里其他任何东西都能靠重读 git 重建；memory 不能，
  因为它是人说过的话。删掉数据库文件就会删掉里面的 memory，别的任何东西都找不回来——
  所以替换这个文件、或清理缓存目录之前，先把它复制一份，或者跑一次 `memory export`
  把写出的文件收好。**import 还没有做**：导出的文件可以读、可以保存，但这个版本里没有
  任何东西能把它读回数据库。
- **仓库搬家要三步，工具没法缩短它。** 数据库是按路径命名的，所以换到新路径的检出会去找
  另一个文件。回来的路是 `analyze --db <旧文件>` 再 `memory adopt --from <旧路径>`，写在
  上面的"仓库搬家的时候"一节里。自己去找旧文件，等于要猜两个路径是同一个项目，而这个工具
  不做这种猜测。
- **两个方向上都还没有增量。** `analyze` 重扫全部历史，把每个提交、每个父提交、每一行
  文件改动都重写一遍——二十万提交时，它 85% 的时间就是这次重写。AST 这一趟每次都会走遍
  所有 Python 文件版本：它不解析已经解析过的、也不写已经存好的，但仍然要把每个 blob 和
  每个存下的 definition 读回来才知道这一点。
- **大仓库要花真金白银的时间和内存。** 二十万提交的历史，第一次 `ast` 约十三分钟、峰值
  约 4 GB 内存，产出约 2 GB 的数据库。更小的历史在每个维度上都按比例更小：十万提交约
  七分钟、2 GB 内存、1 GB 数据库。
- **结构层的体积约是它所依据的 Git 事实的八倍**，因为它是快照而不是变更日志：每个版本的
  每个 definition 都存下来，而不是只存变过的那些。
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
- `commit` 没有 JSON 形式，想要一个提交的事实，程序只能去解析文本块；另外六条读侧命令
  都支持 `--json`。
- 数据库以仓库绝对路径为键，所以移动或重命名仓库后需要重新分析。
- 结构层只读 Python，而且一个 definition 靠「同一个文件里的限定名」确定身份。改了名的
  函数是一次死亡加一次出生，而不是一个「变化过的」definition；搬到另一个文件里的
  definition 也不会和它离开的那个建立联系。这两条都是刻意的：建立这种联系是在做证据
  支持不了的推断。
- 解析不了的文件版本会带着解析器的原话记为 unreadable，绝不会记成「一个空文件」。它的
  definitions 在一个能解析的版本出现之前都是未知的，也不会因为这次失败而被报成删除。
- **co-change 分数是关于提交的统计，不是关于代码的断言。** 它是「这个文件的提交里有多大
  比例动到了对方」，它不对称，而且两个文件一起动并不能证明其中任何一个依赖另一个。一次
  改动超过 `--large-commit-limit`（默认 100）个文件的提交会被排除在所有样本之外，共享
  提交少于 `--min-shared`（默认 2）次的一对会被隐藏；两个计数都会报出来，所以「被排除的
  提交」和「没显示的配对」不会被读成「什么都没找到」。
- 第二趟 `ast` 不解析任何东西、也不写入任何东西，但仍然要把每个 blob 和每个存下的
  definition 读回来比较，所以约是第一趟的三分之一：十万提交时 110s 对 395s。
- 每一行都带着读它的那个 Python 版本**和**比较它的那个分析器版本，所以由更早的解释器或
  更早的分析器写入的数据库会把那些版本重新读一遍，而不是把两个生产者的结果混在一起。
  因此，改动「一个 definition 如何渲染或如何比较」会让所有已存的行失效，下一次 `ast`
  会重新解析。
- 一个 definition 是按它自身的结构比较的，不是按位置：只在文件里挪了地方的函数是
  `unchanged`。
- **`explain` 是唯一需要机器之外的东西的命令。** 没配置模型时它打印证据并以 0 退出；配置了
  就把那份证据发给端点、把回来的东西展示出来。工具里其他任何部分都不依赖它，分析本身永远
  不离开仓库。
- **解释是解读，不是历史事实；它被检查的是引用，不是真伪。** 引用了 bundle 里没有的东西
  会被整份拒绝，候选原因也被挡在 summary 之外——但一句话如果确实落在它引用的那条证据之内、
  却把那句话说过头了，程序抓不住。文本块第一行就把这件事说了出来，而这个检查和它的限度写在
  `docs/v0.4-explanation-schema.md` §6 与 `docs/v0.4-problem-definition.md` §12 里。
- **模型写的东西不会被存下来。** 解释是打印出来给人读的，从不写回数据库，所以以后不会有哪条
  命令把一条解读当成行事实读走。它是否有一天会被存下来是一个未决问题，而这就是为什么它不是一个
  小问题。
- `explain --json` 总会在答案旁边带上证据，但一旦配置了模型，就没有以块的形式单独查看证据的
  开关——离线路径是唯一的入口。
- 发给模型的东西有两重上限。选样会把一个大提交裁到它最大的那些条目——在 benchmark 造出的
  最宽形状上约 11,000 估算 token，而那份 bundle 是 358,000——超过
  `CODEARCHAEOLOGY_AI_MAX_CONTEXT_TOKENS` 时命令会拒绝，而不是发一份会被端点截断的证据。
- **选样保留的是最大的改动，这是一条关于体积的规则，不是关于重要性的。** 纯改名不动任何行，
  所以在一个大到需要裁剪的提交里它会排在文件列表最后，是最先被丢掉的；`selection` 块里的
  计数会说明跟着它一起丢了多少行。这里没有任何东西判断一次改动*意味着*什么，这个块写出来的
  目的就是不让读者去猜缺了什么。
- 由 CI 在 Linux 与 Windows、Python 3.11 与 3.13 下验证；macOS 尚未验证。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
