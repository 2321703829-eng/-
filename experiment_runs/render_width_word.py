"""Render explicit report pages with a local fallback when LibreOffice is absent."""
import sys, pathlib, copy, io
sys.path.insert(0,str(pathlib.Path(__file__).parent/'report_tools'))
import aspose.words as aw
import pypdfium2 as pdfium
from docx import Document
from docx.oxml.ns import qn
ROOT=pathlib.Path(__file__).resolve().parent
source=ROOT.parent/'汇报材料/多智能体宽度200条审核汇总.docx'
out=ROOT/'width_200/failure_analysis/word_render';out.mkdir(exist_ok=True)
doc=Document(source);chunks=[[]]
for el in doc._element.body:
    if el.tag==qn('w:sectPr'):continue
    if el.xpath('.//w:br[@w:type="page"]'):
        chunks.append([])
    else:chunks[-1].append(copy.deepcopy(el))
number=0
for i,chunk in enumerate(chunks,1):
    part=Document(source)
    body=part._element.body
    for el in list(body):
        if el.tag!=qn('w:sectPr'):body.remove(el)
    for el in chunk:body.insert(len(body)-1,el)
    docx=out/f'part-{i}.docx';part.save(docx)
    adoc=aw.Document(str(docx));pdf=out/f'part-{i}.pdf';adoc.save(str(pdf))
    rendered=pdfium.PdfDocument(str(pdf))
    print('part',i,'pages',len(rendered))
    for j in range(len(rendered)):
        number+=1
        page=rendered[j];bitmap=page.render(scale=1.6);img=bitmap.to_pil();img.save(out/f'page-{number}.png')
        img.close();bitmap.close();page.close()
    rendered.close()
print('total',number)
