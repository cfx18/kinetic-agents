"""Create Feishu-importable DOCX, Markdown and standalone HTML from verified tables."""
from pathlib import Path
import json,re,base64,hashlib
from datetime import datetime,timezone
import markdown
from bs4 import BeautifulSoup,NavigableString,Tag
from docx import Document
from docx.shared import Cm,Pt,RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from PIL import Image

ROOT=Path(__file__).resolve().parent
NAME='自主机理压缩_理论与实验结果_20260916'
blocks=json.loads((ROOT/'report_blocks.json').read_text())
blocks['SNAPSHOT']=json.loads((ROOT/'evidence/index.json').read_text())['collected_at_utc']
source=(ROOT/'report_template.md').read_text()
for k,v in blocks.items():source=source.replace('{{'+k+'}}',v)
assert not re.search(r'\{\{[A-Z_]+\}\}',source)
(ROOT/(NAME+'.md')).write_text(source,encoding='utf-8')
body=markdown.markdown(source,extensions=['tables','fenced_code'])
soup=BeautifulSoup(body,'html.parser')
doc=Document();sec=doc.sections[0]
sec.page_width=Cm(21);sec.page_height=Cm(29.7)
sec.top_margin=Cm(1.8);sec.bottom_margin=Cm(1.8);sec.left_margin=Cm(1.7);sec.right_margin=Cm(1.7)
for style in doc.styles:
    if style.type==1 or style.type==2:
        try:
            style.font.name='Noto Sans CJK SC'
            style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Noto Sans CJK SC')
        except AttributeError:pass
normal=doc.styles['Normal'];normal.font.size=Pt(10)
normal.paragraph_format.space_after=Pt(6)
normal.paragraph_format.line_spacing=1.2
for name,size in [('Title',24),('Heading 1',18),('Heading 2',14),('Heading 3',11.5)]:
    doc.styles[name].font.size=Pt(size);doc.styles[name].font.color.rgb=RGBColor.from_string('173654')
    doc.styles[name].paragraph_format.space_before=Pt(13)
    doc.styles[name].paragraph_format.space_after=Pt(7)
header=sec.header.paragraphs[0]
header.text='Agentic Kinetics  /  研究滚动报告  /  内部工作稿'
header.style=doc.styles['Caption']
footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
footer.add_run('2026-09-16  ·  ')
field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)

def inline(p,node,bold=False,italic=False):
    if isinstance(node,NavigableString):
        r=p.add_run(str(node));r.bold=bold;r.italic=italic;return
    if not isinstance(node,Tag):return
    if node.name=='br':p.add_run().add_break();return
    if node.name=='img':return
    for child in node.children:
        inline(p,child,bold or node.name in ['strong','b'],italic or node.name in ['em','i'])

def no_row_split(row):
    trPr=row._tr.get_or_add_trPr();trPr.append(OxmlElement('w:cantSplit'))

for node in soup.children:
    if not isinstance(node,Tag):continue
    if node.name in ['h1','h2','h3','h4']:
        level=int(node.name[1]);style='Title' if level==1 else 'Heading '+str(level-1)
        p=doc.add_paragraph(style=style);inline(p,node)
    elif node.name=='p':
        img=node.find('img')
        if img:
            path=ROOT/img['src'];w,h=Image.open(path).size
            width=min(17.4,22.5*w/h)
            doc.add_picture(str(path),width=Cm(width))
            doc.paragraphs[-1].alignment=WD_ALIGN_PARAGRAPH.CENTER
            cap=doc.add_paragraph(img.get('alt',''),style='Caption');cap.alignment=WD_ALIGN_PARAGRAPH.CENTER
        else:
            p=doc.add_paragraph();inline(p,node)
    elif node.name in ['ul','ol']:
        for li in node.find_all('li',recursive=False):
            p=doc.add_paragraph(style='List Bullet' if node.name=='ul' else 'List Number');inline(p,li)
    elif node.name=='table':
        trs=node.find_all('tr');cols=max(len(tr.find_all(['td','th'],recursive=False)) for tr in trs)
        t=doc.add_table(rows=0,cols=cols);t.style='Table Grid';t.autofit=False
        for column in t.columns:column.width=Cm(17.4/cols)
        for i,tr in enumerate(trs):
            cells=t.add_row().cells
            for cell,td in zip(cells,tr.find_all(['td','th'],recursive=False)):
                p=cell.paragraphs[0];p.paragraph_format.space_after=Pt(3);p.paragraph_format.space_before=Pt(3);p.paragraph_format.line_spacing=1.05
                inline(p,td,bold=i==0)
                for r in p.runs:r.font.size=Pt(8 if cols>=5 else 9)
                # Allow wrapping long artifact identifiers without a giant table.
                pPr=p._p.get_or_add_pPr();wrap=OxmlElement('w:wordWrap');wrap.set(qn('w:val'),'on');pPr.append(wrap)
                if i==0:
                    tcPr=cell._tc.get_or_add_tcPr();shd=OxmlElement('w:shd');shd.set(qn('w:fill'),'E8F0F7');tcPr.append(shd)
            no_row_split(t.rows[-1])
            if i==0:
                h=OxmlElement('w:tblHeader');t.rows[0]._tr.get_or_add_trPr().append(h)
        doc.add_paragraph().paragraph_format.space_after=Pt(2)

doc.core_properties.title='自主化学机理压缩：理论框架、实验结果与失败模式'
doc.core_properties.subject='USC-II / 610 cases / Solo–Team / ordinary–expert prompts'
doc.core_properties.author='AgenticRL 项目研究整理'
doc.core_properties.comments='Internal evidence-backed working report. Not a pristine blind benchmark or an RSI causal proof.'
doc.save(ROOT/(NAME+'.docx'))

# Single-file HTML keeps images intact when opened locally.
for img in soup.find_all('img'):
    data=(ROOT/img['src']).read_bytes();img['src']='data:image/png;base64,'+base64.b64encode(data).decode()
css='''body{max-width:1080px;margin:36px auto;padding:0 28px;color:#243446;font:16px/1.8 "Noto Sans CJK SC",sans-serif}h1,h2,h3{color:#173654;line-height:1.4}h2{border-bottom:1px solid #dfe7ee;padding-bottom:8px;margin-top:38px}table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.6;margin:20px 0}th,td{padding:8px;border:1px solid #dae2e9;overflow-wrap:anywhere}th{background:#e8f0f7;text-align:left}tr:nth-child(even){background:#f8fafc}img{max-width:100%;height:auto}code{font-size:.9em;overflow-wrap:anywhere}strong{color:#142e47}@media print{body{font-size:11px;margin:0}h2,h3{break-after:avoid}tr{break-inside:avoid}thead{display:table-header-group}}'''
(ROOT/(NAME+'.html')).write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+doc.core_properties.title+'</title><style>'+css+'</style><body>'+str(soup)+'</body></html>')
print(json.dumps({'docx':str(ROOT/(NAME+'.docx')),'markdown':str(ROOT/(NAME+'.md')),'paragraphs':len(doc.paragraphs),'tables':len(doc.tables),'figures':len(doc.inline_shapes),'characters':len(source)},ensure_ascii=False))
