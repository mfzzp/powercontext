"""原创题库：Python、Git、数据库三个主题，每主题至少 4 题。"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Question:
    topic: str
    qid: str
    question: str
    answer: str
    knowledge: str


QUESTIONS: tuple[Question, ...] = (
    # ── Python ──
    Question(
        topic="python", qid="P1",
        question="Python 中 list 和 tuple 的主要区别是什么？各自适用场景？",
        answer="list 可变，tuple 不可变。list 适合需要增删元素的集合；tuple 适合固定数据序列，可作 dict 键，也更安全。",
        knowledge="Python 可变与不可变类型",
    ),
    Question(
        topic="python", qid="P2",
        question="解释 Python 的 GIL（全局解释器锁），它对多线程有什么影响？",
        answer="GIL 使同一时刻只有一个线程执行 Python 字节码，因此 CPU 密集型任务无法利用多线程并行。I/O 密集型任务仍可用多线程，因为等待 I/O 时释放 GIL。真正并行需用 multiprocessing。",
        knowledge="Python GIL 与并发模型",
    ),
    Question(
        topic="python", qid="P3",
        question="Python 装饰器（decorator）的原理是什么？请写一个计时装饰器的核心思路。",
        answer="装饰器是接受函数并返回函数的高阶函数，@syntax 等价于 func = decorator(func)。计时装饰器在 wrapper 中记录 time.perf_counter() 前后差值并打印。",
        knowledge="Python 高阶函数与闭包",
    ),
    Question(
        topic="python", qid="P4",
        question="什么是 Python 的上下文管理器（with 语句）？如何自定义？",
        answer="上下文管理器确保资源在进入和退出块时正确分配和释放。可过 __enter__/__exit__ 类方法或 contextlib.contextmanager 生成器实现。",
        knowledge="Python 上下文管理器协议",
    ),
    Question(
        topic="python", qid="P5",
        question="Python 中 == 和 is 的区别？什么时候该用哪个？",
        answer="== 比较值相等（调用 __eq__），is 比较对象身份（同一内存地址）。判断单例如 None 用 is；判断值相等用 ==。",
        knowledge="Python 对象身份与相等性",
    ),

    # ── Git ──
    Question(
        topic="git", qid="G1",
        question="git merge 和 git rebase 有什么区别？各自适用场景？",
        answer="merge 保留分支历史，生成合并提交；rebase 将提交移到目标分支顶端，产生线性历史。公共分支用 merge，个人特性分支可用 rebase 保持整洁。",
        knowledge="Git 分支合并策略",
    ),
    Question(
        topic="git", qid="G2",
        question="git stash 的作用是什么？使用时需要注意什么？",
        answer="stash 暂存当前未提交的修改，便于切换分支。注意：默认不含未跟踪文件（需 -u）；stash 是栈结构，pop 可能冲突；不要长期依赖 stash 代替分支。",
        knowledge="Git 暂存与工作区管理",
    ),
    Question(
        topic="git", qid="G3",
        question="如何解决 git 冲突？请描述完整流程。",
        answer="1) git pull/fetch + merge 触发冲突；2) 打开冲突文件，找到 <<<<<<< / ======= / >>>>>>> 标记；3) 手动选择保留内容；4) git add 标记已解决；5) git commit 完成合并。",
        knowledge="Git 冲突解决流程",
    ),
    Question(
        topic="git", qid="G4",
        question="git reset --soft / --mixed / --hard 的区别？",
        answer="都移动 HEAD 指针。--soft 保留暂存区；--mixed（默认）清空暂存区但保留工作区；--hard 同时清空暂存区和工作区，改动不可恢复。",
        knowledge="Git 重置模式",
    ),
    Question(
        topic="git", qid="G5",
        question=".gitignore 规则生效的时机？已经被跟踪的文件如何忽略？",
        answer=".gitignore 只对未跟踪文件生效。已跟踪文件需先 git rm --cached <file> 移出索引，再在 .gitignore 中添加规则。",
        knowledge="Git 忽略规则",
    ),

    # ── 数据库 ──
    Question(
        topic="database", qid="D1",
        question="什么是数据库索引？有什么优缺点？",
        answer="索引是加速查询的数据结构（常见 B+ 树）。优点：加速 WHERE、JOIN、ORDER BY。缺点：占用额外存储，降低 INSERT/UPDATE 性能，需权衡选择性高的列建索引。",
        knowledge="数据库索引原理",
    ),
    Question(
        topic="database", qid="D2",
        question="解释事务的 ACID 特性。",
        answer="Atomicity 原子性（全做或全不做）；Consistency 一致性（约束不变）；Isolation 隔离性（并发事务互不干扰）；Durability 持久性（提交后不丢失）。",
        knowledge="事务 ACID",
    ),
    Question(
        topic="database", qid="D3",
        question="SQL 中 INNER JOIN、LEFT JOIN、RIGHT JOIN 的区别？",
        answer="INNER JOIN 只返回两表匹配行；LEFT JOIN 返回左表所有行，右表不匹配填 NULL；RIGHT JOIN 返回右表所有行，左表不匹配填 NULL。",
        knowledge="SQL JOIN 类型",
    ),
    Question(
        topic="database", qid="D4",
        question="什么是 SQL 注入？如何防御？",
        answer="攻击者在输入中嵌入 SQL 代码改变查询语义。防御：使用参数化查询（prepared statements），不拼接用户输入；最小权限原则；输入校验。",
        knowledge="SQL 注入与防御",
    ),
    Question(
        topic="database", qid="D5",
        question="数据库范式 1NF、2NF、3NF 分别要求什么？",
        answer="1NF：列不可再分，无重复组；2NF：满足 1NF 且非主属性完全依赖于主键（消除部分依赖）；3NF：满足 2NF 且非主属性不传递依赖于主键（消除传递依赖）。",
        knowledge="数据库范式",
    ),
)

TOPICS = sorted({q.topic for q in QUESTIONS})
BY_ID = {q.qid: q for q in QUESTIONS}
BY_TOPIC: dict[str, list[Question]] = {}
for _q in QUESTIONS:
    BY_TOPIC.setdefault(_q.topic, []).append(_q)


def question_summary(q: Question) -> dict:
    d = asdict(q)
    d.pop("answer")
    return d


def topic_overview(topic: str) -> dict:
    return {
        "topic": topic,
        "count": len(BY_TOPIC.get(topic, [])),
        "questions": [question_summary(q) for q in BY_TOPIC.get(topic, [])],
    }
