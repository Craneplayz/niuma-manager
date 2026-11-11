"""Synthetic assignment-context cases; no real course or attachment data."""

from ddl_text.tests.classification_cases import case, task


HOMEWORK_HINTS = {"section": "课程作业", "source_kind": "content"}


CASES = [
    case("homework-file", "第六讲作业题\n已附加文件：第六讲作业题.pdf（文件未读取）",
         [task(review=True, status="unknown")], text_quality="partial",
         upstream_hints=HOMEWORK_HINTS),
    case("homework-submit", "第二章书面作业\n已附加文件：第二章练习.pdf（文件未读取）\n"
         "请提交 PDF，可以是电子版或纸质版拍照转 PDF。",
         [task(review=True, status="unknown")], text_quality="partial",
         upstream_hints=HOMEWORK_HINTS),
    case("homework-dated", "第二周作业\n内容：课后练习1至3题。截止2026年10月8日。",
         [task("2026-10-08")], upstream_hints=HOMEWORK_HINTS),
    case("homework-answers", "第六讲作业参考答案，仅供复习，不是新布置的作业。",
         [], "information", upstream_hints=HOMEWORK_HINTS),
    case("homework-materials", "参考讲义与往年题展示，本页面只作资料存档。",
         [], "information", upstream_hints={"section": "教学内容", "source_kind": "content"}),
    case("homework-optional", "有兴趣可做拓展练习，不计入课程要求。",
         [task(review=True)], upstream_hints=HOMEWORK_HINTS),
    case("homework-cancel", "第二周作业取消，不再要求完成。",
         [task(notice="cancel", review=True)], upstream_hints=HOMEWORK_HINTS),
    case("homework-multiple", "第二周作业：课后练习1至3题，2026年10月8日截止。\n"
         "课程反馈问卷：2026年10月9日前填写。",
         [task("2026-10-08"), task("2026-10-09")], upstream_hints=HOMEWORK_HINTS),
]
