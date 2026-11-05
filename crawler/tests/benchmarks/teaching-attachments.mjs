import { writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import process from 'node:process';
import console from 'node:console';
import { TextEncoder } from 'node:util';
import { performance } from 'node:perf_hooks';
// Run from crawler/. Only synthetic downloads; real bounded PDF workers.
const root = process.cwd();
const { enrichSourceAttachments } = await import(pathToFileURL(path.join(root,'src/pku-attachments.ts')));
const { extractCourseSourcesFromHtml } = await import(pathToFileURL(path.join(root,'src/pku-source.ts')));
const stream = 'BT /F1 12 Tf 50 700 Td (Submit before 2026-12-31.) Tj ET';
const objects = ['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`];
let doc='%PDF-1.4\n'; const offsets=[0];
objects.forEach((value,i)=>{offsets.push(doc.length);doc+=`${i+1} 0 obj\n${value}\nendobj\n`;});
const start=doc.length;
doc+=`xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(x=>`${String(x).padStart(10,'0')} 00000 n \n`).join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`;
const file={bytes:new TextEncoder().encode(doc), contentType:'application/pdf'};
const sources=Array.from({length:10},(_,i)=>extractCourseSourcesFromHtml(
    `<ul id="content_listContainer"><li id="item${i}"><div class="item"><h3>HW${i}</h3></div><div class="details"><p>Submit the attachment.</p><a href="https://course.pku.edu.cn/bbcswebdav/pid-${i+1}-dt-content-rid-2/HW${i}.pdf">HW${i}.pdf</a></div></li></ul>`,
    {course:'Synthetic',section:'Homework',sourceUrl:'https://course.pku.edu.cn/course?course_id=c1',collectedAt:'2026-10-17T12:00:00+08:00'})[0]);
const elapsed=[];let output;
for(let i=0;i<3;i++){
 let reads=0;const began=performance.now();
 output=await enrichSourceAttachments(sources,async()=>{reads++;return file;});
 elapsed.push((performance.now()-began)/1000);
 if(reads!==10 || output.some(s=>!s.references[0].content_read || !s.references[0].text.includes('2026-12-31'))) throw new Error('bad output');
}
if(process.argv[2]) await writeFile(process.argv[2], JSON.stringify(output));
console.log(JSON.stringify({seconds:elapsed,median_seconds:elapsed.sort((a,b)=>a-b)[1]}));
