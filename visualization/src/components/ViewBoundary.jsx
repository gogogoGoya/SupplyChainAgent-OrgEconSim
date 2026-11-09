import React from 'react';
import LoadingState from './LoadingState';

export function ViewLoading({label='Loading this view…'}){
 return <LoadingState className="view-loading" label={label} variant="chart" description="Preparing the selected view. Navigation remains available."/>;
}

function errorDescription(error){
 if(typeof error?.message==='string'&&error.message.trim())return error.message;
 if(typeof error==='string'&&error.trim())return error;
 return 'An unexpected rendering error interrupted this view.';
}

export default class ViewBoundary extends React.Component{
 state={hasError:false,error:null,attempt:0};
 static getDerivedStateFromError(error){return {hasError:true,error};}
 componentDidUpdate(previous){if(previous.resetKey!==this.props.resetKey&&this.state.hasError)this.setState({hasError:false,error:null});}
 render(){
  const wholeViewer=this.props.scope==='viewer';
  if(this.state.hasError)return <section className="view-error" role="alert">
   <h3>{wholeViewer?'The viewer could not be displayed':'This view could not be displayed'}</h3><p>{errorDescription(this.state.error)}</p>
   <div><button onClick={()=>this.setState(s=>({hasError:false,error:null,attempt:s.attempt+1}))}>{wholeViewer?'Retry viewer':'Retry this view'}</button><button onClick={()=>window.location.reload()}>Reload viewer</button></div>
   <p>{wholeViewer?'Retry to reopen the viewer, or reload to get the current release.':'Other views remain available. Reload the viewer if a newer release was installed.'}</p>
  </section>;
  return <React.Fragment key={this.state.attempt}>{this.props.children}</React.Fragment>;
 }
}
