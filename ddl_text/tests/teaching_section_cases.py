"""Synthetic notices proving that a teaching-web section is not a task category."""
from ddl_text.tests.classification_cases import case, task


def section_case(name, section, text, expected, route='tasks', **metadata):
    return case(name, text, expected, route,
                upstream_hints={'section': section, 'source_kind': 'content'}, **metadata)


CASES = [
    section_case('section-assignment', '课程作业', '请于2026年10月5日18点前提交第一次作业。',
                 [task('2026-10-05T18:00:00+08:00')]),
    section_case('section-notice', '课程通知', '请于2026年10月6日前提交课程内容回顾，请大家尽快完成。',
                 [task('2026-10-06')]),
    section_case('content-hidden-deadline', '教学内容', '第二讲教学材料已上传。本讲附加练习请于2026年10月7日9点前提交。',
                 [task('2026-10-07T09:00:00+08:00')]),
    section_case('content-undated', '教学内容', '请完成本章课后练习并提交答案。', [task()]),
    section_case('content-information', '教学内容', '第三讲课件已上传，内容为虚拟内存的概念和示例。', [], 'information'),
    section_case('notice-postpone', '课程通知', '第一次作业原定2026年10月5日提交，现延期到2026年10月8日。',
                 [task('2026-10-08', 'postpone', True)]),
    section_case('notice-cancel', '课程通知', '取消本次课程报告提交，大家不需要再交本次课程报告。',
                 [task(notice='cancel', review=True)]),
    section_case('content-history', '教学内容', '请于2024年10月1日前提交本章练习。', [], 'filtered_history',
                 published_at={'value': '2024-09-30', 'precision': 'date', 'basis': 'source'}),
]
