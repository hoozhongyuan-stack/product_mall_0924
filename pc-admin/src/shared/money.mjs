export function yuanToFen(value) {
 if(typeof value!=='string'||!/^\d{1,11}(\.\d{1,2})?$/.test(value))return null
 const [whole,decimal='']=value.split('.'),fen=Number(whole)*100+Number(decimal.padEnd(2,'0'))
 return Number.isSafeInteger(fen)&&fen<=9000000000000?fen:null
}
