import React from 'react';

const DaySelector=({availableDays=[],currentDay,onDayChange,floating=false,disabled=false})=>{
  const index=availableDays.indexOf(currentDay);
  return <nav className={`day-selector ${floating?'round-navigation-dock':''}`} aria-label="Round navigation">
    <button disabled={disabled||index<=0} onClick={()=>onDayChange(availableDays[index-1])}>Previous</button>
    <label>Round <select value={currentDay} onChange={event=>onDayChange(Number(event.target.value))} disabled={disabled||!availableDays.length}>
      {availableDays.map(day=><option key={day} value={day}>R{day}</option>)}
    </select></label>
    <button disabled={disabled||index<0||index>=availableDays.length-1} onClick={()=>onDayChange(availableDays[index+1])}>Next</button>
    <small>{availableDays.length} recorded rounds</small>
  </nav>;
};
export default DaySelector;
