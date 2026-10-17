'use strict';
const learningFrame=document.getElementById('learning-frame');
if(learningFrame){
  function fitLearningFrame(){
    try{const doc=learningFrame.contentDocument;if(doc&&doc.body){
      const height=Math.max(doc.documentElement.scrollHeight,doc.body.scrollHeight);
      if(height>200)learningFrame.style.height=(height+40)+'px';
    }}catch(_){/* Leave the usable fixed-size frame in place. */}
  }
  learningFrame.addEventListener('load',fitLearningFrame);
  setInterval(fitLearningFrame,1500);
}
