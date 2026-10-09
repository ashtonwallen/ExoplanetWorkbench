import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-dist-min'
import type { Data, Layout } from 'plotly.js'

export default function Chart({data, layout = {}, height = 300, label}: {data: (Data & {zmid?: number})[]; layout?: Partial<Layout>; height?: number; label: string}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const el = ref.current
    const axis = {gridcolor:'#202b39', zerolinecolor:'#334055', color:'#7f91a7', tickfont:{size:10}, title:{font:{size:11,color:'#8a9bb0'}}, automargin:true}
    void Plotly.react(el, data, {paper_bgcolor:'transparent', plot_bgcolor:'transparent', font:{family:'Inter, Segoe UI, sans-serif',color:'#a9b7c8',size:11}, margin:{l:60,r:22,t:15,b:45}, height, autosize:true, hovermode:'closest', showlegend:false, ...layout, xaxis:{...axis,...layout.xaxis}, yaxis:{...axis,...layout.yaxis}}, {responsive:true, displaylogo:false, scrollZoom:true, modeBarButtonsToRemove:['lasso2d','select2d'], toImageButtonOptions:{format:'png',filename:'exodiscovery-plot',scale:2}})
    const observer = new ResizeObserver(() => { void Plotly.Plots.resize(el) })
    observer.observe(el)
    return () => observer.disconnect()
  }, [data, layout, height])
  useEffect(() => () => { if(ref.current) Plotly.purge(ref.current) }, [])
  return <div ref={ref} role="img" aria-label={label} className="chart" style={{height}} />
}
