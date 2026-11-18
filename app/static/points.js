const sheet={busy:false};const output=document.querySelector('#result');document.querySelector('#calculate').addEventListener('click',async()=>{if(sheet.busy)return;sheet.busy=true;output.textContent='计算中…';try{const dataset=document.querySelector('#dataset').value;const response=await fetch(dataset===document.body.dataset.source?'/api/runs/'+document.body.dataset.runId+'/rerun':'/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset})});const data=await response.json();if(!response.ok)throw Error(data.detail);const url=new URL(location.href);url.searchParams.set('dataset',dataset);url.searchParams.set('run_id',data.manifest.run_id);location.href=url;}catch(e){output.textContent=e.message;}finally{sheet.busy=false;}});document.querySelector('#member-filter').addEventListener('input',e=>document.querySelectorAll('.primary-panel tbody tr').forEach(row=>row.hidden=!row.textContent.includes(e.target.value)));document.querySelectorAll('td[tabindex]').forEach(cell=>cell.addEventListener('focus',()=>document.querySelector('#selection').textContent=cell.textContent));document.querySelector('#dataset').value=new URL(location.href).searchParams.get('dataset')||document.querySelector('#dataset').value;
const filePicker=document.querySelector('#csv-file');
if(filePicker){
 const editor=document.querySelector('#csv-text');
 filePicker.addEventListener('change',async()=>{const file=filePicker.files[0];if(!file)return;if(file.size>2000000){editor.value='';alert('文件超过2MB，请缩小后导入');return;}const bytes=await file.arrayBuffer();try{editor.value=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch(e){editor.value=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}});
 document.querySelector('#import-csv').addEventListener('click',async event=>{event.target.disabled=true;try{const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv_text:editor.value})});const data=await response.json();if(!response.ok)throw Error(data.detail);location.href=location.pathname+'?run_id='+data.manifest.run_id;}catch(e){editor.setCustomValidity(e.message);editor.reportValidity();}finally{event.target.disabled=false;}});
 editor.addEventListener('input',()=>editor.setCustomValidity(''));
}
const publishButton=document.querySelector('#publish');
if(publishButton){
 const result=document.querySelector('#publish-result');
 const backfillEditor=document.querySelector('#backfill-text');
 publishButton.addEventListener('click',async()=>{
   publishButton.disabled=true;result.textContent='发布中…';
   try{
     const response=await fetch('/api/runs/'+document.body.dataset.runId+'/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
     const data=await response.json();
     if(!response.ok)throw Error(data.detail);
     result.textContent='已发布 '+data.length+' 户，可发布余额合计 '+data.reduce((n,p)=>n+p.publishable_remaining,0);
     location.reload();
   }catch(e){result.textContent=e.message;publishButton.disabled=false;}
 });
 document.querySelector('#import-backfill').addEventListener('click',async event=>{
   event.target.disabled=true;result.textContent='重算中…';
   try{
     const body={dataset:document.body.dataset.source,backfill_csv:backfillEditor.value};
     const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
     const data=await response.json();
     if(!response.ok)throw Error(data.detail);
     location.href='/backfill?run_id='+data.manifest.run_id;
   }catch(e){result.textContent=e.message;event.target.disabled=false;}
 });
}
