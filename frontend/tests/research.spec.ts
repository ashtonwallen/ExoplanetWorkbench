import { test, expect } from '@playwright/test'

test('automated research exposes goal, limits and real configuration without making provider calls',async({page})=>{
  const errors:string[]=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.goto('/')
  await page.getByRole('button',{name:'Automated research',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Automated research'})).toBeVisible()
  await page.getByRole('button',{name:'Compare sky regions'}).click()
  await expect(page.getByLabel('What should the AI investigate?')).toHaveValue(/ecliptic poles/)
  await page.getByText('Run limits',{exact:true}).click()
  await expect(page.getByLabel('Scientific operations',{exact:true})).toHaveValue('10')
  await expect(page.getByLabel('Targets',{exact:true})).toHaveValue('3')
  await page.screenshot({path:'test-results/automated-research.png',fullPage:true})
  await page.setViewportSize({width:800,height:1000})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
})

test('scripted UI transport verifies submission, saved evidence, stop and resume controls',async({page})=>{
  // Browser-only fixtures. They never enter the application's database or provider.
  let started=false
  let submitted:any
  let status='running'
  const id='0123456789abcdef'
  await page.route('**/api/settings/ai',route=>route.fulfill({json:{provider:'local',model:'test-fixture',key_configured:false,budget_usd:1}}))
  await page.route('**/api/research',async route=>{
    if(route.request().method()==='POST'){
      submitted=route.request().postDataJSON();started=true
      await route.fulfill({json:{id}})
    }else await route.fulfill({json:started?[{id,goal:submitted.goal,status,created:new Date().toISOString()}]:[]})
  })
  await page.route('**/api/research/'+id,route=>route.fulfill({json:{id,status,request:submitted,phase:'Reviewing computed evidence',progress_text:'Reading saved investigation',
    provider:{provider:'local',model:'test-fixture'},outcome:null,error:null,targets:['synthetic'],operations_used:1,model_calls:3,input_mb:2.1,estimated_usd:0,reserved_usd:0,
    plan:{strategy:'Synthetic UI test strategy.',steps:['Read observations','Review the computed evidence'],selection_reason:'Test fixture only.'},
    discoveries:[],findings:[{id:'abcdef0123456789',target:'Synthetic UI fixture',parameters:{min_period:.5,max_period:10,sectors:[2]},signals:[{index:0,period:3.18,depth_ppm:900,classification:'Insufficient evidence'}]}],
    actions:[{call_id:'1',name:'read_investigation',status:'completed',started:new Date().toISOString(),arguments:'{}',result:{status:'test fixture'}}],notes:[],conclusion:null}}))
  await page.route('**/api/jobs/'+id+'/*',route=>{status=route.request().url().endsWith('/cancel')?'cancelled':'queued';return route.fulfill({json:{id,status}})})
  await page.goto('/')
  await page.getByRole('button',{name:'Automated research',exact:true}).click()
  await page.getByRole('button',{name:'Recover a known planet'}).click()
  await page.getByRole('button',{name:'Start research',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Investigation plan'})).toBeVisible()
  expect(submitted.goal).toContain('WASP-18')
  expect(submitted.max_operations).toBe(10)
  await expect(page.getByText('3.180000 d · 900 ppm')).toBeVisible()
  await page.getByText('Analysis parameters chosen by AI').click()
  await expect(page.locator('pre').filter({hasText:'min_period'})).toContainText('0.5')
  await page.getByRole('button',{name:'Stop',exact:true}).click()
  await expect(page.getByRole('button',{name:'Resume',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Resume',exact:true}).click()
  await expect(page.getByRole('button',{name:'Stop',exact:true})).toBeVisible()
  await expect(page.getByRole('link',{name:'Report',exact:true})).toHaveAttribute('href','/api/research/'+id+'/report')
})
