# CodeArchaeology

**一台理解代码如何演化的时间机器。**

[English](README.md) | [简体中文](README.zh-CN.md)

> **状态：v0.3.1，已完成并冻结。** 下面九个命令现在就能用：v0.1 带来的三条，v0.2 的
> `hotspots`、`files` 和 `file`，以及 v0.3 的 `ast`、`structure` 和 `cochange`。
> v0.3.x 是什么、不是什么，写在
> [`docs/v0.3-final-state.md`](docs/v0.3-final-state.md) 里。项目还没有
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
archaeology 0.3.1
```

## 用法

所有命令都接受一个"仓库内的目录"作为参数，默认是当前目录。**它们都不会往被分析的
仓库里写任何东西**——分析结果写进你缓存目录里的数据库。

读侧的命令——`timeline`、`hotspots`、`files`、`file`、`structure`、`cochange`——支持
`--json`，用同样的字段名把同样的事实输出给别的程序读。stdout 上只有 JSON——「分析结果
已过期」的提示走 stderr——所以无论快照是不是最新的，输出都能被解析。`commit` 目前还没有
JSON 形式；两个写入方 `analyze` 和 `ast` 打印的是这次做了什么。

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
那种情况会整表重建，结构层也在内——之后要再跑一次 `ast`。

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
重建，而那是在造一个新库，不是对同一个库重扫。

### 数据库放在哪

数据库文件名是仓库绝对路径的摘要，所以同一个项目的两份检出各有一份库。

| 平台 | 位置 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\codearchaeology\` |
| macOS | `~/Library/Caches/codearchaeology/` |
| Linux 及其他 | `$XDG_CACHE_HOME/codearchaeology/`，没有该变量时用 `~/.cache/codearchaeology/` |

设置环境变量 `CODEARCHAEOLOGY_CACHE_DIR` 可以换地方，或者给任意命令加 `--db` 直接
指定文件。

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
    cli.py          Typer 应用与九个命令
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
    formatting.py   两个视图共用的小工具
tests/
    sample_repo.py  构造一个确定性的小仓库供测试使用
    conftest.py     把这个仓库交给每个测试的 fixture
benchmarks/
    benchmark.py    Git 各操作与 AST 这一趟，规模由你指定
    cochange_benchmark.py  co-change 分析随历史增长的成本
    index_benchmark.py     每个数据库索引买到了什么、花了什么
```

## 核心原则

以下六条不是口号，它们约束代码被允许怎么写。

1. **Core First** —— 核心不能依赖 LLM。任何功能都必须能完全不用 AI 跑通。
   AI 只是上面的一层，永远不是地基。
2. **Local First** —— 任何 Git 仓库都必须能在本地分析，不依赖远程服务。
3. **Evidence First** —— 任何结论都必须能追溯到具体的 commit / diff / AST 节点。
   禁止凭空推测。
4. **Python-only v0.x** —— AST 分析读的是 Python。
   多语言支持在路线图上，不在当前范围内。
5. **先 CLI，后 Web** —— v0.x 的界面就是命令行，没有 Web UI。
6. **SQLite 是唯一存储** —— v0.x 不引入 PostgreSQL / Redis / 向量数据库。

## 路线图

| 阶段 | 增加什么 | 状态 |
|---|---|---|
| v0.1 | Git 扫描、提交历史、文件改动、SQLite 存储、CLI 时间线 | 已完成 |
| v0.2 | 文件生命周期、代码热点，以及给别的程序读的 JSON 输出 | 已完成 |
| v0.3 | AST 分析、函数与类的演化，以及 co-change | 已完成 |
| v0.4 | 基于证据层的 AI 解释（Provider 可替换） | 计划中 |
| v0.5 | Developer Memory —— 你自己的技术使用轨迹 | 计划中 |
| v0.6 | AI 参与的改动分析与回放 | 计划中 |

每个阶段一次只做一个，前一个稳定之前不设计后一个。

每个版本以及它改了什么，记录在 [CHANGELOG](CHANGELOG.md) 里。

## 已知限制

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
- 由 CI 在 Linux 与 Windows、Python 3.11 与 3.13 下验证；macOS 尚未验证。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
