// Register only the chart families and components used by the review interface.
import * as echarts from 'echarts/core';
import {BarChart, LineChart, PieChart, GraphChart, HeatmapChart, SankeyChart, CustomChart} from 'echarts/charts';
import {GridComponent, TooltipComponent, LegendComponent, TitleComponent, MarkLineComponent,
 MarkAreaComponent, MarkPointComponent, VisualMapComponent, DataZoomComponent,
 ToolboxComponent, GraphicComponent, DatasetComponent, TransformComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
echarts.use([BarChart,LineChart,PieChart,GraphChart,HeatmapChart,SankeyChart,CustomChart,
 GridComponent,TooltipComponent,LegendComponent,TitleComponent,MarkLineComponent,
 MarkAreaComponent,MarkPointComponent,VisualMapComponent,DataZoomComponent,
 ToolboxComponent,GraphicComponent,DatasetComponent,TransformComponent,CanvasRenderer]);
export * from 'echarts/core';
