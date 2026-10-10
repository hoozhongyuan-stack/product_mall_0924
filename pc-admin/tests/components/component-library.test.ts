import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ComponentLibrary from '../../src/views/pages/ComponentLibrary.vue'
it('keeps common choices short while retaining all thirteen component kinds',async()=>{
  const w=mount(ComponentLibrary,{props:{disabled:false}})
  expect(w.findAll('.home-component-library button')).toHaveLength(4)
  const seen=new Set<string>()
  for(const group of w.findAll('.component-library-groups button')){
    await group.trigger('click')
    expect(group.attributes('aria-pressed')).toBe('true')
    for(const choice of w.findAll('.home-component-library button')){
      await choice.trigger('click');seen.add(String(w.emitted('add')!.at(-1)![0]))
    }
  }
  expect(seen.size).toBe(13)
  await w.setProps({disabled:true})
  const count=w.emitted('add')!.length
  await w.get('.home-component-library button').trigger('click')
  expect(w.emitted('add')).toHaveLength(count)
  w.unmount()
})
