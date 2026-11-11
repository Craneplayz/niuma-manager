import { shiftDay } from "./task-model.js";
export function demoWorkspace(today) {
  const definitions = [
    [
      "概率论作业",
      "概率论",
      -1,
      "23:59",
      "教学网",
      "完成第 3 章习题，并在教学网提交 PDF。",
    ],
    [
      "数据结构实验报告",
      "数据结构",
      0,
      "18:00",
      "教学网",
      "整理实验结果，附上运行截图与分析。",
    ],
    [
      "英语课堂阅读",
      "大学英语",
      0,
      "20:00",
      "文本导入",
      "阅读指定材料，准备课堂讨论。",
    ],
    ["线性代数作业", "线性代数", 1, "23:59", "教学网", "完成本周习题。"],
    ["计算机导论报告", "计算机导论", 3, "18:00", "教学网", "提交课程报告。"],
    [
      "物理实验预习",
      "大学物理",
      6,
      "",
      "文本导入",
      "阅读实验讲义，准备实验记录表。",
    ],
    [
      "整理参考资料",
      "",
      null,
      "",
      "手动添加",
      "把本周用到的参考资料整理到一起。",
    ],
  ];
  const tasks = definitions.map(
    ([title, course, offset, dueTime, source, notes], i) => ({
      id: "preview-" + i,
      title,
      course,
      due: offset === null ? "" : shiftDay(today, offset),
      dueTime,
      source,
      notes,
      status: "todo",
    }),
  );
  tasks.push({
    id: "preview-done",
    title: "提交课程选题",
    course: "计算机导论",
    due: shiftDay(today, -2),
    dueTime: "18:00",
    source: "教学网",
    notes: "",
    status: "done",
    completedAt: shiftDay(today, -3),
  });
  tasks.push(
    {
      id: "preview-notice-room",
      content_type: "notice",
      title: "本周上课地点调整为理教 408",
      course: "数据结构",
      due: "",
      dueTime: "",
      source: "教学网",
      notes: "本周课程在理教 408 上课，课程时间不变。",
      status: "todo",
    },
    {
      id: "preview-notice-result",
      content_type: "notice",
      title: "课程分组结果公示",
      course: "计算机导论",
      due: today,
      dueTime: "18:00",
      source: "教学网",
      notes: "课程分组结果公示至今天 18:00。这是公示有效期，不是提交作业的截止时间。",
      status: "todo",
    },
  );
  return {
    tasks,
    trash: [],
  };
}
