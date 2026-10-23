"""Optional source-backed title and course-calendar facts for teaching v0.4."""
KINDS={'assignment':'作业','lab':'实验','exam':'考试','reading':'阅读','notice':'通知','other':'事项'}


def fact_schemas(quote):
    def obj(properties):
        return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
    text=lambda n:dict(type='string',maxLength=n)
    evidence=dict(type='array',items=quote,minItems=1,maxItems=10)
    title=obj(dict(kind=dict(enum=list(KINDS)),channel=text(64),sequence=text(128),subject=text(160),evidence=evidence))
    calendar=obj(dict(week_one_monday=dict(type='string',format='date'),week=dict(type='integer',minimum=1,maximum=53),
        weekday=dict(type='integer',minimum=1,maximum=7),offset_days=dict(type='integer',minimum=-14,maximum=14),evidence=evidence))
    return {name:dict(anyOf=[value,dict(type='null')]) for name,value in [('title_facts',title),('calendar_rule',calendar)]}
