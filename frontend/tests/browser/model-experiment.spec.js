import {test,expect} from '@playwright/test';

test('every second result opens feedback; both results can be rated independently',async ({page},info)=>{
 await page.clock.install();
 let ready=1, claimed=0, reads=0;
 const votes=[];
 const results=[1,2].map(ordinal=>({id:String(ordinal).repeat(32),module:'text',at:1795660000+ordinal,ordinal}));
 await page.route('**/api/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  let json={};
  if(path==='/api/auth/me')json={user:{id:'experiment-synthetic',name:'合成账号',email:'example@example.test'}};
  if(path==='/api/workspace')json={tasks:{version:1,value:[]}};
  if(path==='/api/recognition/batches')json=[];
  if(path==='/api/usage/visit')json={offerRating:false,modelExperiment:true};
  if(path==='/api/usage/model-experiment'&&req.method()==='GET'){reads++;json={results:results.slice(0,ready).reverse(),promptDue:ready===2&&!claimed};}
  if(path==='/api/usage/model-experiment/prompt'){claimed++;json={result:results[1]};}
  if(path==='/api/usage/model-experiment'&&req.method()==='POST'){votes.push(req.postDataJSON());json={saved:true};}
  await route.fulfill({json});
 });
 await page.goto('/');
 await expect.poll(()=>reads).toBeGreaterThan(0);
 await expect(page.locator('#model-experiment-prompt')).toHaveCount(0);
 ready=2;
 await page.clock.fastForward(12000);
 const prompt=page.getByRole('dialog',{name:'评价模型结果'});
 await expect(prompt).toBeVisible();
 await prompt.screenshot({path:info.outputPath('experiment-prompt.png')});
 await expect(prompt.getByRole('textbox')).toHaveCount(0);
 await prompt.getByRole('button',{name:'2分',exact:true}).click();
 await expect(prompt).toHaveCount(0);
 if(await page.locator('#sidebar-show').isVisible()&&!await page.locator('.account-entry').isVisible())await page.locator('#sidebar-show').click();
 await page.getByRole('button',{name:'账号与数据',exact:true}).click();
 await page.getByText('评价已有模型结果',{exact:true}).click();
 const history=page.locator('[data-experiment-results]');
 await expect(history.locator('form')).toHaveCount(2);
 await history.locator('form').last().getByRole('button',{name:'5分',exact:true}).click();
 await expect(history.getByText('已评价：5 分')).toBeVisible();
 expect(votes).toEqual([{id:results[1].id,score:2},{id:results[0].id,score:5}]);
 expect(claimed).toBe(1);
});
